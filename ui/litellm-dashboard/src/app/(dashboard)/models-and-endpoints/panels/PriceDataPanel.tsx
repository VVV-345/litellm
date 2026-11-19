"use client";

import PriceDataManagementTab from "@/app/(dashboard)/models-and-endpoints/components/PriceDataManagementTab";
import { useSearchParams } from "next/navigation";

export default function PriceDataPanel() {
  const search = useSearchParams()?.get("price_model") ?? "";
  return <PriceDataManagementTab key={search} initialSearch={search} />;
}
