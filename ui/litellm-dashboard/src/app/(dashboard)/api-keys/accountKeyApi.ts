import { apiClient } from "@/components/networking";
import type { components } from "@/lib/http/schema";
type CardKeyStatus = components["schemas"]["CardKeyStatus"];
type CardKeyIssue = components["schemas"]["CardKeyIssue"];

const cardPath = (cardId: string) => `/account_pool/cards/${encodeURIComponent(cardId)}/key`;
export const getCardKeyStatus = (accessToken: string, cardId: string) =>
  apiClient.get<CardKeyStatus | null>(`${cardPath(cardId)}/status`, { accessToken });

export const issueCardKey = (accessToken: string, cardId: string, expectedKeyId?: string) =>
  apiClient.post<CardKeyIssue>(`${cardPath(cardId)}${expectedKeyId ? "/rotate" : ""}`, {
    accessToken,
    ...(expectedKeyId ? { body: { expected_key_id: expectedKeyId } } : {}),
  });

export const revokeCardKey = (accessToken: string, cardId: string, expectedKeyId: string) =>
  apiClient.delete<void>(cardPath(cardId), { accessToken, body: { expected_key_id: expectedKeyId } });
