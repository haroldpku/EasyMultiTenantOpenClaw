# GitHub Actions

## Claude Code (`claude.yml`)

Lets you mention `@claude` in issues / PR comments / reviews and have Claude
read the repo and respond (or open a PR) automatically.

### Required secrets

Set these in **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Your Anthropic API key (official or third-party proxy) |
| `ANTHROPIC_BASE_URL` | optional | Third-party Anthropic-compatible proxy URL, e.g. `https://your-proxy.example.com/v1`. Leave unset to use the official `https://api.anthropic.com`. |

### Verify it works

After pushing the workflow:

1. Open a new issue titled `@claude hello`
2. Within ~30 s the action should run; you'll see a check mark + a reply from Claude

If the action errors out, check **Actions tab → Claude Code → failed run → logs**.
Common failure modes:

- Missing `ANTHROPIC_API_KEY` secret
- `ANTHROPIC_BASE_URL` reachable from your proxy vendor's IP allowlist but
  not from GitHub runner IPs — verify the proxy allows unauthenticated
  ingress from `140.82.112.0/20` (GitHub Actions range)
- Free / personal Anthropic accounts may need billing enabled
