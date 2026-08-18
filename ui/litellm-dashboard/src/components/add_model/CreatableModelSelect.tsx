// 本文件把项目现有的多选组件封装为模型选择器，统一支持候选模型和自定义模型名。
import { MultiSelect, type MultiSelectOption } from "@/components/shared/MultiSelect";

interface CreatableModelSelectProps {
  value?: string[];
  models: string[];
  placeholder: string;
  extraOptions?: MultiSelectOption[];
  disabled?: boolean;
  onChange?: (models: string[]) => void;
  testId?: string;
}

export default function CreatableModelSelect({
  value,
  models,
  placeholder,
  extraOptions = [],
  disabled = false,
  onChange,
  testId = "creatable-model-select",
}: CreatableModelSelectProps) {
  const knownOptions = Array.from(new Set(models))
    .sort((left, right) => left.localeCompare(right))
    .map((model) => ({ label: model, value: model }));
  const options = Array.from(
    new Map([...extraOptions, ...knownOptions].map((option) => [option.value, option])).values(),
  );

  return (
    <div data-testid={testId}>
      <MultiSelect
        options={options}
        value={value}
        placeholder={placeholder}
        allowCustomValues
        disabled={disabled}
        onValueChange={(nextModels) => onChange?.(nextModels)}
      />
    </div>
  );
}
