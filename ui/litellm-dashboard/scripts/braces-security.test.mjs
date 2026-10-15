import assert from "node:assert/strict";
import { test } from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const braces = require(process.env.BRACES_TARGET || "braces");

test("ordinary patterns retain expansion, escaping and range semantics", () => {
  assert.deepEqual(braces.expand("src/{a,b}/{1..3}.ts"), [
    "src/a/1.ts", "src/a/2.ts", "src/a/3.ts", "src/b/1.ts", "src/b/2.ts", "src/b/3.ts",
  ]);
  assert.equal(braces.compile("src/{a,b}.ts"), "src/(a|b).ts");
  assert.doesNotThrow(() => braces.compile("{".repeat(128) + "a,b" + "}".repeat(128)));
  assert.doesNotThrow(() => braces.parse('"' + "{".repeat(256) + '"'));
  assert.doesNotThrow(() => braces.parse("[" + "{".repeat(256) + "]"));
  assert.doesNotThrow(() => braces.parse("\\{".repeat(256)));
});

for (const [open, close] of [["{", "}"], ["(", ")"], ["{(", ")}"]]) {
  for (const operation of ["parse", "compile", "expand", "stringify"]) {
    test(`${operation} rejects nested ${open} before recursive traversal`, () => {
      assert.throws(() => braces[operation](open.repeat(129) + "a,b" + close.repeat(129)), {
        name: "SyntaxError", message: "Pattern nesting exceeds 128 levels",
      });
      assert.throws(() => braces[operation](open.repeat(4000)), {
        name: "SyntaxError", message: "Pattern nesting exceeds 128 levels",
      });
    });
  }
}


test("installed micromatch and fast-glob use the patched parser", () => {
  const fromGlob = createRequire(require.resolve("fast-glob"));
  const fromMicromatch = createRequire(fromGlob.resolve("micromatch"));
  assert.equal(fromMicromatch.resolve("braces"), require.resolve("braces"));
  const micromatch = fromGlob("micromatch");
  assert.deepEqual(micromatch.braceExpand("src/{a,b}.ts"), ["src/a.ts", "src/b.ts"]);
  assert.throws(() => micromatch.braceExpand("{".repeat(4000) + "a,b" + "}".repeat(4000)), {
    name: "SyntaxError", message: "Pattern nesting exceeds 128 levels",
  });
  for (const pattern of ["(".repeat(128), "{(".repeat(64)]) {
    assert.doesNotThrow(() => braces.parse(pattern));
  }
});
