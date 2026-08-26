type RandomValuesGenerator = (values: Uint8Array) => Uint8Array;

interface UuidCrypto {
  randomUUID?: () => string;
  getRandomValues?: RandomValuesGenerator;
}

const fallbackUuid = (random: () => number): string =>
  "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
    const value = Math.floor(random() * 16);
    const nibble = character === "x" ? value : (value & 0x3) | 0x8;
    return nibble.toString(16);
  });

const cryptoUuid = (getRandomValues: RandomValuesGenerator): string => {
  const bytes = getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

export const generateRequestUuid = (
  crypto: UuidCrypto | undefined = globalThis.crypto,
  random: () => number = Math.random,
): string => {
  if (typeof crypto?.randomUUID === "function") return crypto.randomUUID();
  if (typeof crypto?.getRandomValues === "function") return cryptoUuid(crypto.getRandomValues.bind(crypto));
  return fallbackUuid(random);
};
