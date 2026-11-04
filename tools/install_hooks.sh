#!/bin/sh
# Install the secret/phone-number checks as git hooks for this clone.
set -e
root=$(git rev-parse --show-toplevel)
cat > "$root/.git/hooks/pre-commit" <<'EOF'
#!/bin/sh
exec python3 "$(git rev-parse --show-toplevel)/tools/check_secrets.py"
EOF
cat > "$root/.git/hooks/commit-msg" <<'EOF'
#!/bin/sh
exec python3 "$(git rev-parse --show-toplevel)/tools/check_secrets.py" --message "$1"
EOF
chmod +x "$root/.git/hooks/pre-commit" "$root/.git/hooks/commit-msg"
echo "Installed pre-commit and commit-msg secret checks."
