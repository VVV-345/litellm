import ast,re,hashlib
from pathlib import Path
r=Path.cwd();p=r/"litellm/proxy/management_endpoints/account_pool_cockpit_subject.py"
tree=ast.parse(p.read_text(encoding="utf-8"))
query=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id=="_LIVE_QUERY")
schema=(r/"schema.prisma").read_text(encoding="utf-8")
blocks=dict(re.findall(r"^model\s+(\w+)\s*\{(.*?)^\}",schema,re.M|re.S))
models={name:{m.group(1):m.group(2) for line in body.splitlines() if (m:=re.match(r"\s*(\w+)\s+(\w+(?:\[\])?\??)",line))} for name,body in blocks.items()}
aliases={"k":"LiteLLM_VerificationToken","u":"LiteLLM_UserTable","t":"LiteLLM_TeamTable","p":"LiteLLM_ProjectTable","m":"LiteLLM_TeamMembership"}
refs=set(re.findall(r"\b([kutpm])\.(\w+)\b",query))
for alias,column in refs:assert column in models[aliases[alias]],(alias,column)
arrays=re.findall(r"cardinality\(([kutpm])\.(\w+)\)",query)
for alias,column in arrays:assert models[aliases[alias]][column].rstrip("?").endswith("[]")
jsons=re.findall(r"([kutpm])\.(\w+)\s*=\s*'\{\}'::jsonb",query)
for alias,column in jsons:assert models[aliases[alias]][column].rstrip("?")=="Json"
for alias,column in [("k","token"),("u","user_id"),("t","team_id"),("p","project_id")]:assert re.search(r"^\s*"+column+r"\s+[^\n]*@id\b",blocks[aliases[alias]],re.M)
assert re.search(r"@@id\(\[user_id,\s*team_id\]\)",blocks[aliases["m"]])
assert '$1' in query and 'LIMIT 1' in query and 'NOT EXISTS (SELECT 1 FROM "LiteLLM_DeletedVerificationToken" WHERE token = $1)' in query
print(f"Schema reference check: PASS; {len(refs)} distinct qualified columns, {len(arrays)} array predicates, {len(jsons)} JSON predicates")
print("All joined keys unique under schema.prisma; PostgreSQL execution/Prisma decoding NOT tested")
print("Subject SHA256: "+hashlib.sha256(p.read_bytes()).hexdigest())
