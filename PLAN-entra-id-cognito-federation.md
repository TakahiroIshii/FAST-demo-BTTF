# Plan: Federate Microsoft Entra ID with Cognito User Pool

## Goal

Allow users to sign in via their Microsoft Entra ID (Azure AD) corporate identity, in addition to the existing Cognito local user login. This uses Cognito's OIDC Identity Provider federation.

## Prerequisites (User Must Complete)

Before we can deploy, the user needs to register an app in Microsoft Entra ID:

1. Go to **Azure Portal → Microsoft Entra ID → App registrations → New registration**
2. Set redirect URI to: `https://<cognito-domain>.auth.<region>.amazoncognito.com/oauth2/idpresponse`
3. Under **Certificates & secrets**, create a new client secret
4. Note down:
   - **Tenant ID** (from Overview page)
   - **Client ID** (Application ID from Overview page)
   - **Client Secret** (the value, not the secret ID)

## Changes

### 1. Add Entra ID config fields to `config.yaml` and `AppConfig`

Add an optional `entra_id` section to `config.yaml`:

```yaml
# Optional: Microsoft Entra ID (Azure AD) federation
# Uncomment and fill in to enable corporate SSO login
# entra_id:
#   tenant_id: "your-azure-tenant-id"
#   client_id: "your-azure-app-client-id"
#   client_secret: "your-azure-app-client-secret"
```

Update `AppConfig` interface in `config-manager.ts` to include:

```typescript
entra_id?: {
  tenant_id: string
  client_id: string
  client_secret: string
}
```

### 2. Update `cognito-stack.ts`

When `config.entra_id` is provided:

- Add a `UserPoolIdentityProviderOidc` construct pointing to Entra ID's OIDC endpoints:
  - Issuer: `https://login.microsoftonline.com/<tenant_id>/v2.0`
  - Scopes: `openid`, `email`, `profile`
  - Attribute mapping: map Entra ID claims to Cognito attributes (email, name, etc.)
- Update the `UserPoolClient` to include the new identity provider via `supportedIdentityProviders`
- Expose the `userPool` object as a class property (currently it's local to the method) so the IdP can reference it

### 3. No frontend changes needed

The frontend already uses standard OIDC flows via `react-oidc-context`. Cognito's hosted UI handles the "Sign in with Microsoft" button automatically when a federated IdP is configured. No code changes required.

### 4. No `deploy-frontend.py` changes needed

The `aws-exports.json` structure stays the same — authority, client_id, redirect_uri, etc. The federation is entirely server-side in Cognito.

## Deployment

```bash
cd infra-cdk
cdk deploy
```

Then redeploy frontend to pick up any config changes:

```bash
python scripts/deploy-frontend.py
```

## Security Notes

- The Entra ID client secret is passed as a plain string in CDK. For production, consider storing it in AWS Secrets Manager and referencing it via `SecretValue.secretsManager()`. For this demo/dev setup, inline config is acceptable.
- `selfSignUpEnabled` remains `false` — federated users are auto-created in Cognito on first login but cannot self-register.

## Rollback

If Entra ID config is removed from `config.yaml`, the next `cdk deploy` will remove the OIDC provider and revert to local-only auth. No data loss.

## Files Modified

1. `infra-cdk/lib/utils/config-manager.ts` — add `entra_id` to `AppConfig` interface and parsing
2. `infra-cdk/config.yaml` — add commented-out `entra_id` section
3. `infra-cdk/lib/cognito-stack.ts` — add OIDC IdP, update client, expose userPool
