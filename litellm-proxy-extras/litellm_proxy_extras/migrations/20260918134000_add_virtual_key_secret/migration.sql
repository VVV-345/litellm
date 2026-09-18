CREATE TABLE "LiteLLM_VirtualKeySecret" (
    "token" TEXT NOT NULL,
    "ciphertext" TEXT NOT NULL,
    CONSTRAINT "LiteLLM_VirtualKeySecret_pkey" PRIMARY KEY ("token"),
    CONSTRAINT "LiteLLM_VirtualKeySecret_token_fkey" FOREIGN KEY ("token") REFERENCES "LiteLLM_VerificationToken"("token") ON DELETE CASCADE ON UPDATE CASCADE
);
