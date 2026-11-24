import { readFileSync } from "node:fs";
import ts from "typescript";

function accountPoolKeys(language) {
  const path = new URL(`../src/locales/${language}.json`, import.meta.url);
  const text = readFileSync(path, "utf8");
  JSON.parse(text);
  const root = ts.parseJsonText(path.pathname, text).statements[0].expression;
  const groups = root.properties.filter((property) => property.name.text === "accountPool");
  if (groups.length !== 1) throw new Error(`${language}: expected one accountPool object`);
  const keys = new Set();
  function visit(node, prefix) {
    const names = new Set();
    for (const { name, initializer } of node.properties) {
      const key = `${prefix}.${name.text}`;
      if (names.has(name.text)) throw new Error(`${language}: duplicate ${key}`);
      names.add(name.text);
      if (ts.isObjectLiteralExpression(initializer)) visit(initializer, key);
      else keys.add(key);
    }
  }
  visit(groups[0].initializer, "accountPool");
  return keys;
}

const en = accountPoolKeys("en");
const zh = accountPoolKeys("zh-CN");
for (const key of new Set([...en, ...zh])) {
  if (!en.has(key) || !zh.has(key)) throw new Error(`Missing translation: ${key}`);
}
console.log(`accountPool: ${en.size} keys in each language; 0 duplicate or missing keys`);
