/** Primary label for the navbar account control — avoids raw placeholder JWT/user IDs in the UI. */
export function navAccountDisplayName(
  userEmail: string | null,
  userId: string | null,
  translate: (key: string) => string = (key) => key,
): string {
  const email = userEmail?.trim();
  if (email) {
    return email;
  }
  const id = userId?.trim();
  if (!id) {
    return translate("Account");
  }
  if (/^default[_\s-]?user[_\s-]?id$/i.test(id)) {
    return translate("Account");
  }
  return id;
}
