export interface GuardrailSettings {
  supported_entities: string[];
  supported_actions: string[];
  supported_modes: string[];
  supported_modes_by_provider?: Record<string, string[]>;
  pii_entity_categories: Array<{
    category: string;
    entities: string[];
  }>;
  content_filter_settings?: {
    prebuilt_patterns: Array<{
      name: string;
      display_name: string;
      category: string;
      description: string;
    }>;
    pattern_categories: string[];
    supported_actions: string[];
    content_categories?: Array<{
      name: string;
      display_name: string;
      description: string;
      default_action: string;
    }>;
  };
}
