# Plan: PDF Search Gateway Tool (Back to the Future Script)

## Goal
Add a new AgentCore Gateway tool that reads a PDF from S3 and answers questions about its content. This is a demo tool for querying a Back to the Future movie script.

## Approach
Following the existing FAST Gateway pattern (docs/GATEWAY.md), we'll create:

1. **New tool folder**: `gateway/tools/pdf_search_tool/` (mirrors `sample_tool/` structure)
2. **Lambda function**: Reads PDF from S3, extracts text, searches for relevant content
3. **Tool spec JSON**: Defines the tool schema for the gateway
4. **CDK updates**: Add new Lambda + gateway target in `backend-stack.ts`
5. **S3 bucket**: For storing the PDF, deployed via CDK
6. **PDF location**: User places their PDF at `gateway/tools/pdf_search_tool/data/` — CDK will upload it to S3 during deploy

## Architecture
- Gateway receives `search_pdf` tool call with a `question` parameter
- Gateway routes to new Lambda target
- Lambda reads PDF from S3, extracts text with `pypdf`, finds relevant sections
- Lambda returns matching content as the tool response

## Files to Create/Modify

### New Files
- `gateway/tools/pdf_search_tool/__init__.py` — empty init
- `gateway/tools/pdf_search_tool/pdf_search_lambda.py` — Lambda handler
- `gateway/tools/pdf_search_tool/tool_spec.json` — tool schema
- `gateway/tools/pdf_search_tool/requirements.txt` — pypdf dependency
- `gateway/tools/pdf_search_tool/data/` — directory where user places the PDF

### Modified Files
- `infra-cdk/lib/backend-stack.ts` — add S3 bucket, new Lambda (PythonFunction for deps), new gateway target

## Key Design Decisions
- Use `PythonFunction` (not `lambda.Function`) so `pypdf` dependency is bundled automatically
- Simple keyword/sentence search (no vector DB) — this is a demo
- Single PDF support — keeps it simple
- Lambda reads PDF from S3 on each invocation (cold start caches it via `/tmp`)

## PDF Placement
User drops their PDF file at: `gateway/tools/pdf_search_tool/data/back_to_the_future.pdf`
