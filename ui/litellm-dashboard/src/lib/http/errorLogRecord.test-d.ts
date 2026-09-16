import { describe, expectTypeOf, test } from "vitest";

import type { components } from "@/lib/http/schema";

describe("error log record types", () => {
  test("includes the per-request cache rate", () => {
    expectTypeOf<components["schemas"]["ErrorLogRecord"]["cache_rate"]>().toEqualTypeOf<
      number | null | undefined
    >();
  });
});
