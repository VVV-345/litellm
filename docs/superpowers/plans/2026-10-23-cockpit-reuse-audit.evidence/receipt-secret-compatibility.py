import importlib.util,json,sys
from pathlib import Path
from uuid import UUID
r=Path.cwd();tx=Path(__file__).resolve().parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
old=load("receipt_baseline_secrets",tx/"source-baseline/account-pool/account_pool/shared/secrets.py")
new=load("receipt_modified_secrets",r/"account-pool/account_pool/shared/secrets.py")
seed="FIXTURE-ONLY-PURPOSE-REGRESSION";card=UUID(int=1)
before={p.value:old.EnvironmentSecretDeriver(seed).derive(card,p) for p in old.SecretPurpose}
after={p.value:new.EnvironmentSecretDeriver(seed).derive(card,p) for p in new.SecretPurpose}
assert all(after[k]==v for k,v in before.items())
assert after[new.SecretPurpose.COCKPIT_CREDENTIAL_RECEIPT.value] not in before.values()
a=old.StateCipher(old.EnvironmentSecretDeriver(seed));b=new.StateCipher(new.EnvironmentSecretDeriver(seed))
assert a.open(card,b.seal(card,"fixture-state"))=="fixture-state"
assert b.open(card,a.seal(card,"fixture-state"))=="fixture-state"
print(json.dumps({"old_purposes_unchanged":list(before),"new_purpose_distinct":True,"cross_version_state_decryption":True},sort_keys=True))
