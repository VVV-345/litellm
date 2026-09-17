import React, { useMemo, useState } from "react";
import { CircleHelp } from "lucide-react";
import type { TFunction } from "i18next";
import { z } from "zod/v4";
import { toast } from "@/lib/toast";
import { registerClaudeCodePlugin } from "@/components/networking";
import { FieldGroup } from "@/components/ui/field";
import { FormField } from "@/components/shared/form/FormField";
import { Button } from "@/components/ui/button";
import { UiLoadingSpinner } from "@/components/ui/ui-loading-spinner";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useZodForm } from "@/lib/forms/useZodForm";
import {
  validatePluginName,
  isValidSemanticVersion,
  isValidEmail,
  parseKeywords,
  parseSkillSource,
  isValidSubPath,
  SkillSourcePreview,
} from "@/components/claude_code_plugins/helpers";
import { PluginAuthor, PluginSource, SkillRegisterRequest } from "@/components/claude_code_plugins/types";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useTranslation } from "react-i18next";

interface AddPluginFormProps {
  visible: boolean;
  onClose: () => void;
  accessToken: string | null;
  onSuccess: () => void;
}

const createAddPluginSchema = (t: TFunction) =>
  z.object({
    skillUrl: z.string().min(1, t("ui.Please enter a repository URL")),
    subPath: z
      .string()
      .refine(
        (value) => !value || isValidSubPath(value),
        t("ui.Subfolder must be a relative path like plugins/my-skill (letters, numbers, dots, hyphens, underscores)"),
      ),
    name: z
      .string()
      .min(1, t("ui.Please enter skill name"))
      .regex(/^[a-z0-9-]+$/, t("ui.Name must be kebab-case (lowercase, numbers, hyphens only)")),
    domain: z.string(),
    namespace: z.string(),
    description: z.string(),
    category: z.string(),
    keywords: z.string(),
    version: z.string(),
    authorName: z.string(),
    authorEmail: z
      .string()
      .refine((value) => value === "" || z.email().safeParse(value).success, t("ui.Please enter a valid email")),
  });

type AddPluginFormValues = z.infer<ReturnType<typeof createAddPluginSchema>>;

const EMPTY_VALUES: AddPluginFormValues = {
  skillUrl: "",
  subPath: "",
  name: "",
  domain: "",
  namespace: "",
  description: "",
  category: "",
  keywords: "",
  version: "",
  authorName: "",
  authorEmail: "",
};

const buildAuthor = (values: AddPluginFormValues): PluginAuthor | undefined => {
  const name = values.authorName.trim();
  const email = values.authorEmail.trim();
  if (!name) {
    return undefined;
  }
  return email ? { name, email } : { name };
};

const buildRegisterRequest = (values: AddPluginFormValues, source: PluginSource): SkillRegisterRequest => {
  const author = buildAuthor(values);
  return {
    name: values.name.trim(),
    source,
    ...(values.version ? { version: values.version.trim() } : {}),
    ...(values.description ? { description: values.description.trim() } : {}),
    ...(author ? { author } : {}),
    ...(values.category ? { category: values.category } : {}),
    ...(values.keywords ? { keywords: parseKeywords(values.keywords) } : {}),
    ...(values.domain ? { domain: values.domain.trim() } : {}),
    ...(values.namespace ? { namespace: values.namespace.trim() } : {}),
  };
};

const PREDEFINED_CATEGORIES = [
  "Development",
  "Productivity",
  "Learning",
  "Security",
  "Data & Analytics",
  "Integration",
  "Testing",
  "Documentation",
];

const labelWithHint = (label: string, hint: string): React.ReactNode => (
  <>
    {label}
    <Tooltip>
      <TooltipTrigger render={<CircleHelp className="size-3.5 shrink-0 cursor-help text-muted-foreground" />} />
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  </>
);

const AddPluginForm: React.FC<AddPluginFormProps> = ({ visible, onClose, accessToken, onSuccess }) => {
  const { t } = useTranslation();
  const addPluginSchema = useMemo(() => createAddPluginSchema(t), [t]);
  const form = useZodForm(addPluginSchema, { defaultValues: EMPTY_VALUES });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [urlPreview, setUrlPreview] = useState<SkillSourcePreview | null>(null);
  const [urlEncodesSubdir, setUrlEncodesSubdir] = useState(false);

  const recomputePreview = (skillUrl: string, subPath: string) => {
    const encodesSubdir = parseSkillSource(skillUrl)?.parsed.source === "git-subdir";
    setUrlEncodesSubdir(encodesSubdir);
    if (encodesSubdir && form.getValues("subPath")) {
      form.setValue("subPath", "");
    }
    const preview = parseSkillSource(skillUrl, encodesSubdir ? undefined : subPath);
    setUrlPreview(preview);
    if (preview && !form.getValues("name")) {
      form.setValue("name", preview.suggestedName);
    }
  };

  const handleSubmit = async (values: AddPluginFormValues) => {
    if (!accessToken) {
      toast.error(t("ui.No access token available"));
      return;
    }

    if (!urlPreview) {
      toast.error(t("ui.Please enter a valid repository URL"));
      return;
    }

    if (!validatePluginName(values.name)) {
      toast.error(t("ui.Skill name must be kebab-case (lowercase letters, numbers, and hyphens only)"));
      return;
    }

    if (values.version && !isValidSemanticVersion(values.version)) {
      toast.error(t("ui.Version must be in semantic versioning format (e.g., 1.0.0)"));
      return;
    }

    if (values.authorEmail && !isValidEmail(values.authorEmail)) {
      toast.error(t("ui.Invalid email format"));
      return;
    }

    setIsSubmitting(true);
    try {
      await registerClaudeCodePlugin(accessToken, buildRegisterRequest(values, urlPreview.parsed));
      toast.success(t("ui.Skill registered successfully"));
      form.reset(EMPTY_VALUES);
      setUrlPreview(null);
      setUrlEncodesSubdir(false);
      onSuccess();
      onClose();
    } catch (error) {
      console.error("Error registering skill:", error);
      toast.error(error instanceof Error && error.message ? error.message : t("ui.Failed to register skill"));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCancel = () => {
    form.reset(EMPTY_VALUES);
    setUrlPreview(null);
    setUrlEncodesSubdir(false);
    onClose();
  };

  return (
    <Dialog open={visible} onOpenChange={(open) => !open && handleCancel()}>
      <DialogContent className="top-8 max-h-[calc(100dvh-4rem)] translate-y-0 overflow-y-auto sm:max-w-[700px]">
        <DialogHeader>
          <DialogTitle>{t("ui.Add New Skill")}</DialogTitle>
        </DialogHeader>
        <TooltipProvider>
          <form onSubmit={form.handleSubmit(handleSubmit)} noValidate className="mt-4">
            <FieldGroup>
              <FormField
                control={form.control}
                name="skillUrl"
                label={labelWithHint(
                  t("ui.Repository URL"),
                  t(
                    "ui.Paste an HTTPS git repository URL from GitHub, GitLab, Bitbucket, or a self-hosted host. E.g. github.com/org/repo, gitlab.com/org/repo, or github.com/org/repo/tree/main/my-skill",
                  ),
                )}
              >
                {({ ref, onChange, ...field }) => (
                  <Input
                    {...field}
                    ref={ref}
                    placeholder={t("ui.https://github.com/org/repo or https://gitlab.com/org/repo")}
                    className="rounded-lg"
                    onChange={(event) => {
                      onChange(event);
                      recomputePreview(event.target.value, form.getValues("subPath"));
                    }}
                  />
                )}
              </FormField>

              <FormField
                control={form.control}
                name="subPath"
                label={labelWithHint(
                  t("ui.Subfolder path (Optional)"),
                  t(
                    "ui.Path within the repository where the skill lives (e.g., plugins/my-skill). Leave empty if the skill is at the repo root.",
                  ),
                )}
                description={
                  urlEncodesSubdir
                    ? t("ui.The URL already points to a subfolder, so this field is disabled")
                    : undefined
                }
              >
                {({ ref, onChange, ...field }) => (
                  <Input
                    {...field}
                    ref={ref}
                    placeholder={t("ui.plugins/my-skill")}
                    className="rounded-lg"
                    onChange={(event) => {
                      onChange(event);
                      recomputePreview(form.getValues("skillUrl"), event.target.value);
                    }}
                    disabled={urlEncodesSubdir}
                  />
                )}
              </FormField>

              {urlPreview && (
                <div className="rounded-lg border border-info/20 bg-info/10 px-3 py-2 text-sm text-info">
                  {t("ui.Detected:")} {urlPreview.label}
                </div>
              )}

              <FormField
                control={form.control}
                name="name"
                label={labelWithHint(
                  t("ui.Skill Name"),
                  t("ui.Unique identifier in kebab-case format (e.g., my-skill)"),
                )}
              >
                {({ ref, ...field }) => (
                  <Input {...field} ref={ref} placeholder={t("ui.my-skill")} className="rounded-lg" />
                )}
              </FormField>

              <div className="flex gap-4">
                <FormField
                  control={form.control}
                  name="domain"
                  label={labelWithHint(
                    t("ui.Domain (Optional)"),
                    t("ui.Top-level grouping in the Skill Hub (e.g., Productivity)"),
                  )}
                  className="flex-1"
                >
                  {({ ref, ...field }) => (
                    <Input {...field} ref={ref} placeholder={t("ui.Productivity")} className="rounded-lg" />
                  )}
                </FormField>
                <FormField
                  control={form.control}
                  name="namespace"
                  label={labelWithHint(
                    t("ui.Namespace (Optional)"),
                    t("ui.Sub-grouping within domain (e.g., workflows)"),
                  )}
                  className="flex-1"
                >
                  {({ ref, ...field }) => (
                    <Input {...field} ref={ref} placeholder={t("ui.workflows")} className="rounded-lg" />
                  )}
                </FormField>
              </div>

              <FormField
                control={form.control}
                name="description"
                label={labelWithHint(t("ui.Description (Optional)"), t("ui.Brief description of what the skill does"))}
              >
                {({ ref, ...field }) => (
                  <Textarea
                    {...field}
                    ref={ref}
                    rows={3}
                    placeholder={t("ui.A skill that helps with...")}
                    maxLength={500}
                    className="rounded-lg"
                  />
                )}
              </FormField>

              <FormField
                control={form.control}
                name="category"
                label={labelWithHint(t("ui.Category (Optional)"), t("ui.Select a category or enter a custom one"))}
              >
                {({ id, value, onChange, "aria-invalid": ariaInvalid, "aria-describedby": ariaDescribedBy }) => (
                  <Combobox
                    items={PREDEFINED_CATEGORIES}
                    itemToStringLabel={(category: string) => t(`ui.${category}`, { defaultValue: category })}
                    value={value === "" ? null : value}
                    onValueChange={(category: string | null) => onChange(category ?? "")}
                  >
                    <ComboboxInput
                      id={id}
                      aria-invalid={ariaInvalid}
                      aria-describedby={ariaDescribedBy}
                      placeholder={t("ui.Select or type a category")}
                      className="w-full rounded-lg"
                      showClear={value !== ""}
                    />
                    <ComboboxContent>
                      <ComboboxEmpty>{t("ui.No matching categories")}</ComboboxEmpty>
                      <ComboboxList>
                        {(category: string) => (
                          <ComboboxItem key={category} value={category}>
                            {t(`ui.${category}`, { defaultValue: category })}
                          </ComboboxItem>
                        )}
                      </ComboboxList>
                    </ComboboxContent>
                  </Combobox>
                )}
              </FormField>

              <FormField
                control={form.control}
                name="keywords"
                label={labelWithHint(t("ui.Keywords (Optional)"), t("ui.Comma-separated list of keywords for search"))}
              >
                {({ ref, ...field }) => (
                  <Input {...field} ref={ref} placeholder={t("ui.search, web, api")} className="rounded-lg" />
                )}
              </FormField>

              <FormField
                control={form.control}
                name="version"
                label={labelWithHint(t("ui.Version (Optional)"), t("ui.Semantic version (e.g., 1.0.0)"))}
              >
                {({ ref, ...field }) => (
                  <Input {...field} ref={ref} placeholder={t("ui.1.0.0")} className="rounded-lg" />
                )}
              </FormField>

              <FormField
                control={form.control}
                name="authorName"
                label={labelWithHint(t("ui.Author Name (Optional)"), t("ui.Name of the skill author or organization"))}
              >
                {({ ref, ...field }) => (
                  <Input {...field} ref={ref} placeholder={t("ui.Your Name or Organization")} className="rounded-lg" />
                )}
              </FormField>

              <FormField
                control={form.control}
                name="authorEmail"
                label={labelWithHint(t("ui.Author Email (Optional)"), t("ui.Contact email for the skill author"))}
              >
                {({ ref, ...field }) => (
                  <Input
                    {...field}
                    ref={ref}
                    type="email"
                    placeholder={t("ui.author@example.com")}
                    className="rounded-lg"
                  />
                )}
              </FormField>
            </FieldGroup>

            <div className="mt-6 flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={handleCancel} disabled={isSubmitting}>
                {t("ui.Cancel")}
              </Button>
              <Button type="submit" disabled={isSubmitting} aria-busy={isSubmitting}>
                {isSubmitting && <UiLoadingSpinner className="size-4" />}
                {isSubmitting ? t("ui.Adding...") : t("ui.Add Skill")}
              </Button>
            </div>
          </form>
        </TooltipProvider>
      </DialogContent>
    </Dialog>
  );
};

export default AddPluginForm;
