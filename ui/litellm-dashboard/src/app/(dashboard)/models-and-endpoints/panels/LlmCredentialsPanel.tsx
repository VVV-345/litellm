"use client";

import { Form } from "antd";
import { useTranslation } from "react-i18next";
import CredentialsPanel from "@/components/model_add/CredentialsPanel";
import { vertexCredentialsUploadProps } from "@/app/(dashboard)/models-and-endpoints/vertexCredentialsUpload";

export default function LlmCredentialsPanel() {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  return <CredentialsPanel uploadProps={vertexCredentialsUploadProps(form, t)} />;
}
