import React, { useState } from "react";
import type { TFunction } from "i18next";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Eye, EyeOff } from "lucide-react";
import NotificationManager from "./molecules/notifications_manager";
import { serviceHealthCheck, setCallbacksCall } from "./networking";
import { EmailEventSettings } from "./email_events";
import { useTranslation } from "react-i18next";

interface EmailSettingsProps {
  accessToken: string | null;
  premiumUser: boolean;
  alerts: any[];
}

const REQUIRED_MARKER = (t: TFunction) => <span className="text-destructive"> {t("ui.Required")} * </span>;

const getFieldHelp = (t: TFunction): Record<string, React.ReactNode> => ({
  SMTP_HOST: (
    <>
      {t("ui.Enter the SMTP host address, e.g. `smtp.resend.com`")}
      {REQUIRED_MARKER(t)}
    </>
  ),
  SMTP_PORT: (
    <>
      {t("ui.Enter the SMTP port number, e.g. `587`")}
      {REQUIRED_MARKER(t)}
    </>
  ),
  SMTP_USERNAME: (
    <>
      {t("ui.Enter the SMTP username, e.g. `username`")}
      {REQUIRED_MARKER(t)}
    </>
  ),
  SMTP_PASSWORD: REQUIRED_MARKER(t),
  SMTP_SENDER_EMAIL: (
    <>
      {t("ui.Enter the sender email address, e.g. `sender@berri.ai`")}
      {REQUIRED_MARKER(t)}
    </>
  ),
  TEST_EMAIL_ADDRESS: (
    <>
      {t("ui.Email Address to send `Test Email Alert` to. example: `info@berri.ai`")}
      {REQUIRED_MARKER(t)}
    </>
  ),
  EMAIL_LOGO_URL: <>{t("ui.(Optional) Customize the Logo that appears in the email, pass a url to your logo")}</>,
  EMAIL_SUPPORT_CONTACT: (
    <>
      {t(
        "ui.(Optional) Customize the support email address that appears in the email. Default is support@berri.ai",
      )}
    </>
  ),
});

const PREMIUM_ONLY_FIELDS = ["EMAIL_LOGO_URL", "EMAIL_SUPPORT_CONTACT"];

const SENSITIVE_FIELD_PATTERN = /(PASSWORD|SECRET|KEY|TOKEN)/i;

const EmailSettings: React.FC<EmailSettingsProps> = ({ accessToken, premiumUser, alerts }) => {
  const { t } = useTranslation();
  const [visibleFields, setVisibleFields] = useState<Record<string, boolean>>({});

  const toggleFieldVisibility = (key: string) => {
    setVisibleFields((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  };

  const handleSaveEmailSettings = async () => {
    if (!accessToken) {
      return;
    }

    let updatedVariables: Record<string, string> = {};

    alerts
      .filter((alert) => alert.name === "email")
      .forEach((alert) => {
        Object.entries(alert.variables ?? {}).forEach(([key, value]) => {
          const inputElement = document.querySelector(`input[name="${key}"]`) as HTMLInputElement;
          if (!inputElement || !inputElement.value) {
            return;
          }
          // Only send fields the admin actually edited. Values rendered from the
          // server are masked (SMTP_PASSWORD) or sourced from the process
          // environment, so re-submitting an untouched field would persist a mask
          // or copy env-managed config into the database.
          if (inputElement.value === (value == null ? "" : String(value))) {
            return;
          }
          updatedVariables[key] = inputElement.value;
        });
      });

    //filter out null / undefined values for updatedVariables

    const payload = {
      general_settings: {
        alerting: ["email"],
      },
      environment_variables: updatedVariables,
    };
    try {
      await setCallbacksCall(accessToken, payload);
      NotificationManager.success(t("ui.Email settings updated successfully"));
    } catch (error) {
      NotificationManager.fromBackend(error);
    }
  };

  return (
    <>
      <div className="mt-6 mb-6">
        <EmailEventSettings accessToken={accessToken} />
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("ui.Email Server Settings")}</CardTitle>
          <p className="text-sm">
            <a
              href="https://docs.litellm.ai/docs/proxy/email"
              target="_blank"
              rel="noreferrer"
              className="text-primary underline underline-offset-4"
            >
              {t("ui.LiteLLM Docs: email alerts")}
            </a>
          </p>
        </CardHeader>

        <CardContent>
          {alerts
            .filter((alert) => alert.name === "email")
            .map((alert, index) => (
              <div key={index} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {Object.entries(alert.variables ?? {}).map(([key, value]) => {
                  const isLocked = !premiumUser && PREMIUM_ONLY_FIELDS.includes(key);
                  const isSensitive = SENSITIVE_FIELD_PATTERN.test(key);
                  const isVisible = visibleFields[key] || false;
                  return (
                    <div key={key} className="space-y-1">
                      {isLocked ? (
                        <a
                          href="https://forms.gle/W3U4PZpJGFHWtHyA9"
                          target="_blank"
                          rel="noreferrer"
                          className="text-sm text-primary underline underline-offset-4"
                        >
                          ✨ {key}
                        </a>
                      ) : (
                        <p className="text-sm">{key}</p>
                      )}
                      <InputGroup className="max-w-100">
                        <InputGroupInput
                          name={key}
                          defaultValue={value as string}
                          type={isSensitive && !isVisible ? "password" : "text"}
                          disabled={isLocked}
                        />
                        {isSensitive && (
                          <InputGroupAddon align="inline-end">
                            <InputGroupButton
                              size="icon-xs"
                              onClick={() => toggleFieldVisibility(key)}
                              aria-label={isVisible ? t("ui.Hide credential") : t("ui.Show credential")}
                            >
                              {isVisible ? <EyeOff /> : <Eye />}
                            </InputGroupButton>
                          </InputGroupAddon>
                        )}
                      </InputGroup>
                      <div className="text-xs text-muted-foreground italic">{getFieldHelp(t)[key]}</div>
                    </div>
                  );
                })}
              </div>
            ))}

          <div className="mt-6 flex gap-2">
            <Button onClick={() => handleSaveEmailSettings()}>{t("ui.Save Changes")}</Button>
            <Button
              variant="secondary"
              onClick={async () => {
                if (!accessToken) return;
                try {
                  await serviceHealthCheck(accessToken, "email");
                  NotificationManager.success(
                    t("ui.Email test triggered. Check your configured email inbox/logs."),
                  );
                } catch (error) {
                  NotificationManager.fromBackend(error);
                }
              }}
            >
              {t("ui.Test Email Alerts")}
            </Button>
          </div>
        </CardContent>
      </Card>
    </>
  );
};

export default EmailSettings;
