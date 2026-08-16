import React, { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Form, Input, Select, Tooltip } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import { fetchAvailableModels, ModelGroup } from "@/components/llm_calls/fetch_models";

interface S3VectorsConfigProps {
  accessToken: string | null;
  providerParams: Record<string, any>;
  onParamsChange: (params: Record<string, any>) => void;
}

const S3VectorsConfig: React.FC<S3VectorsConfigProps> = ({ accessToken, providerParams, onParamsChange }) => {
  const { t } = useTranslation();
  const [embeddingModels, setEmbeddingModels] = useState<ModelGroup[]>([]);
  const [isLoadingModels, setIsLoadingModels] = useState(false);

  useEffect(() => {
    if (!accessToken) return;

    const loadModels = async () => {
      setIsLoadingModels(true);
      try {
        const models = await fetchAvailableModels(accessToken);
        // Filter for embedding models only
        const embeddingOnly = models.filter((model) => model.mode === "embedding");
        setEmbeddingModels(embeddingOnly);
      } catch (error) {
        console.error("Error fetching embedding models:", error);
      } finally {
        setIsLoadingModels(false);
      }
    };

    loadModels();
  }, [accessToken]);

  const handleFieldChange = (fieldName: string, value: string) => {
    onParamsChange({
      ...providerParams,
      [fieldName]: value,
    });
  };

  return (
    <>
      {/* S3 Vectors Setup Instructions */}
      <Alert
        message={t("ui.AWS S3 Vectors Setup")}
        description={
          <div>
            <p>{t("ui.AWS S3 Vectors allows you to store and query vector embeddings directly in S3:")}</p>
            <ul style={{ marginLeft: "16px", marginTop: "8px" }}>
              <li>
                {t("ui.Vector buckets and indexes will be automatically created if they don't exist")}
              </li>
              <li>{t("ui.Vector dimensions are auto-detected from your selected embedding model")}</li>
              <li>{t("ui.Ensure your AWS credentials have permissions for S3 Vectors operations")}</li>
              <li>
                {t("ui.Learn more:")}{" "}
                <a
                  href="https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vector-buckets.html"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t("ui.AWS S3 Vectors Documentation")}
                </a>
              </li>
            </ul>
          </div>
        }
        type="info"
        showIcon
        style={{ marginBottom: "16px" }}
      />

      {/* Vector Bucket Name */}
      <Form.Item
        label={
          <span>
            {t("ui.Vector Bucket Name")}{" "}
            <Tooltip
              title={t(
                "ui.S3 bucket name for vector storage (must be at least 3 characters, lowercase letters, numbers, hyphens, and periods only)",
              )}
            >
              <InfoCircleOutlined style={{ marginLeft: "4px" }} />
            </Tooltip>
          </span>
        }
        required
        validateStatus={
          providerParams.vector_bucket_name && providerParams.vector_bucket_name.length < 3 ? "error" : undefined
        }
        help={
          providerParams.vector_bucket_name && providerParams.vector_bucket_name.length < 3
            ? t("ui.Bucket name must be at least 3 characters")
            : undefined
        }
      >
        <Input
          value={providerParams.vector_bucket_name || ""}
          onChange={(e) => handleFieldChange("vector_bucket_name", e.target.value)}
          placeholder={t("ui.my-vector-bucket (min 3 chars)")}
          size="large"
          className="rounded-md"
        />
      </Form.Item>

      {/* Index Name (Optional) */}
      <Form.Item
        label={
          <span>
            {t("ui.Index Name")}{" "}
            <Tooltip
              title={t(
                "ui.Name for the vector index (optional, will be auto-generated if not provided). If provided, must be at least 3 characters.",
              )}
            >
              <InfoCircleOutlined style={{ marginLeft: "4px" }} />
            </Tooltip>
          </span>
        }
        validateStatus={
          providerParams.index_name && providerParams.index_name.length > 0 && providerParams.index_name.length < 3
            ? "error"
            : undefined
        }
        help={
          providerParams.index_name && providerParams.index_name.length > 0 && providerParams.index_name.length < 3
            ? t("ui.Index name must be at least 3 characters if provided")
            : undefined
        }
      >
        <Input
          value={providerParams.index_name || ""}
          onChange={(e) => handleFieldChange("index_name", e.target.value)}
          placeholder={t("ui.my-vector-index (optional, min 3 chars)")}
          size="large"
          className="rounded-md"
        />
      </Form.Item>

      {/* AWS Region */}
      <Form.Item
        label={
          <span>
            {t("ui.AWS Region")}{" "}
            <Tooltip title={t("ui.AWS region where the S3 bucket is located (e.g., us-west-2)")}>
              <InfoCircleOutlined style={{ marginLeft: "4px" }} />
            </Tooltip>
          </span>
        }
        required
      >
        <Input
          value={providerParams.aws_region_name || ""}
          onChange={(e) => handleFieldChange("aws_region_name", e.target.value)}
          placeholder="us-west-2"
          size="large"
          className="rounded-md"
        />
      </Form.Item>

      {/* Embedding Model */}
      <Form.Item
        label={
          <span>
            {t("ui.Embedding Model")}{" "}
            <Tooltip title={t("ui.Select the embedding model to use for vector generation")}>
              <InfoCircleOutlined style={{ marginLeft: "4px" }} />
            </Tooltip>
          </span>
        }
        required
      >
        <Select
          value={providerParams.embedding_model || undefined}
          onChange={(value) => handleFieldChange("embedding_model", value)}
          placeholder={t("ui.Select an embedding model")}
          size="large"
          showSearch
          loading={isLoadingModels}
          filterOption={(input, option) => (option?.label ?? "").toLowerCase().includes(input.toLowerCase())}
          options={embeddingModels.map((model) => ({
            value: model.model_group,
            label: model.model_group,
          }))}
          style={{ width: "100%" }}
        />
      </Form.Item>
    </>
  );
};

export default S3VectorsConfig;
