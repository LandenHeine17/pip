#!/bin/sh
# Install the repository checks as git hooks for this clone:
#   pre-commit: no secrets or phone numbers in staged changes; project map mentions every top-level item
#   commit-msg: no secrets or phone numbers in the commit message
set -e
root=$(git rev-parse --show-toplevel)
cat > "$root/.git/hooks/pre-commit" <<'HOOK'
#!/bin/sh
root=$(git rev-parse --show-toplevel)
python3 "$root/tools/check_secrets.py" && python3 "$root/tools/check_map.py"
HOOK
cat > "$root/.git/hooks/commit-msg" <<'HOOK'
#!/bin/sh
exec python3 "$(git rev-parse --show-toplevel)/tools/check_secrets.py" --message "$1"
HOOK
chmod +x "$root/.git/hooks/pre-commit" "$root/.git/hooks/commit-msg"
echo "Installed pre-commit (secrets + project map) and commit-msg (secrets) checks."
