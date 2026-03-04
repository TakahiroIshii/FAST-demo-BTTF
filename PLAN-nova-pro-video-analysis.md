# Plan: Switch to Amazon Nova Pro with Video Upload Support

## Goal

Switch the agent model from Claude to Amazon Nova Pro on Bedrock and add video upload capability to the frontend, enabling users to upload Back to the Future video clips and ask the agent to identify scenes.

## Architecture Overview

The change spans 4 layers: backend agent, infrastructure (CDK), frontend UI, and frontend client library.

**Flow:**
1. User selects a video file in the chat UI and types a question
2. Frontend uploads the video to S3 via a presigned URL (obtained from a new Lambda endpoint)
3. Frontend sends the text prompt + S3 video URI to the agent via `AgentCoreClient.invoke()`
4. Backend agent constructs a multimodal message with `video` + `text` content blocks
5. Agent passes the multimodal message to Nova Pro via `agent.stream_async()`
6. Nova Pro analyzes the video and streams a response back

## Changes

### 1. Backend: Model Swap + Multimodal Input (`patterns/strands-single-agent/basic_agent.py`)

**Model change (1 line):**
```python
# Before
bedrock_model = BedrockModel(model_id="us.anthropic.claude-opus-4-6-v1", temperature=0.1)

# After
bedrock_model = BedrockModel(model_id="us.amazon.nova-pro-v1:0", temperature=0.1)
```

**Multimodal input handling in `agent_stream()`:**
- Extract optional `videoS3Uri` and `videoFormat` from the payload (alongside existing `prompt` and `runtimeSessionId`)
- If video is present, construct a list of content blocks:
  ```python
  content_blocks = []
  if video_s3_uri:
      content_blocks.append({
          "video": {
              "format": video_format or "mp4",
              "source": {
                  "s3Location": {
                      "uri": video_s3_uri,
                      "bucketOwner": os.environ.get("AWS_ACCOUNT_ID")
                  }
              }
          }
      })
  content_blocks.append({"text": user_query})
  ```
- Pass `content_blocks` (instead of plain `user_query` string) to `agent.stream_async()` when video is present
- The Strands SDK `ContentBlock` TypedDict already supports `video` field with `VideoContent` type — no SDK changes needed

**System prompt update:**
Add video analysis instructions to the system prompt so Nova Pro knows it can analyze video content.

### 2. Infrastructure: Video Upload S3 Bucket + Presigned URL Lambda (`infra-cdk/lib/backend-stack.ts`)

**New S3 bucket** for video uploads:
- Bucket name: `${config.stack_name_base}-video-uploads-${account}-${region}`
- CORS configured to allow PUT from the frontend origin
- Lifecycle rule: auto-delete objects after 1 day (videos are ephemeral)
- Block public access, S3-managed encryption

**New Lambda function** for generating presigned upload URLs:
- Path: `infra-cdk/lambdas/video-presign/index.py`
- Accepts `{ fileName, contentType }` in the request body
- Returns `{ uploadUrl, s3Uri, objectKey }` — the presigned PUT URL and the resulting S3 URI
- Presigned URL expires after 5 minutes

**New API Gateway endpoint** (or reuse existing feedback API):
- `POST /video-upload-url` — calls the presigned URL Lambda
- Protected by Cognito authorizer (same as feedback API)

**Agent runtime permissions:**
- Grant the agent runtime role `s3:GetObject` on the video uploads bucket so Nova Pro can read the video via S3 URI

**Environment variable:**
- Add `VIDEO_BUCKET_NAME` to the agent runtime environment variables
- Add `AWS_ACCOUNT_ID` to the agent runtime environment variables (needed for `bucketOwner` in S3 location)

### 3. Frontend: Video Upload UI (`frontend/src/components/chat/ChatInput.tsx`)

**New UI elements:**
- Add a video upload button (paperclip/video icon) next to the Send button
- Hidden `<input type="file" accept="video/mp4,video/webm,video/quicktime">` triggered by the button
- When a file is selected, show a small preview chip (filename + size + remove button) above the textarea
- File size validation: reject files > 1GB (Nova Pro S3 limit)

**New props:**
- `videoFile: File | null` — the selected video file
- `setVideoFile: (file: File | null) => void` — setter for the video file
- These are managed as state in `ChatInterface.tsx` and passed down

### 4. Frontend: Message Flow (`frontend/src/components/chat/ChatInterface.tsx`)

**Video upload flow in `sendMessage()`:**
1. If `videoFile` is set, first call the presigned URL endpoint to get an upload URL
2. Upload the video to S3 via `fetch(uploadUrl, { method: 'PUT', body: videoFile })`
3. Pass the resulting `s3Uri` to `client.invoke()` alongside the text query
4. Clear the video file state after sending

**New helper function:** `getVideoUploadUrl(fileName, contentType, idToken)` — calls the API Gateway endpoint

**Message type update:** Add optional `videoAttachment` to the `Message` interface for display purposes (show a video indicator in the chat history).

### 5. Frontend: Client Library (`frontend/src/lib/agentcore-client/client.ts`)

**Update `invoke()` method signature:**
- Add optional `videoS3Uri?: string` parameter
- Include `videoS3Uri` and `videoFormat` in the JSON body sent to AgentCore Runtime:
  ```typescript
  body: JSON.stringify({
    prompt: query,
    runtimeSessionId: sessionId,
    videoS3Uri: videoS3Uri,      // optional
    videoFormat: "mp4",           // optional
  })
  ```

### 6. Frontend: Types (`frontend/src/components/chat/types.ts`)

**Add to `Message` interface:**
```typescript
videoAttachment?: {
  fileName: string
  fileSize: number
  s3Uri?: string
}
```

### 7. Frontend: Config (`frontend/public/aws-exports.json`)

**Add new field** (populated by CDK output):
- `videoUploadApiUrl` — the API Gateway URL for the presigned URL endpoint

## Files Modified

| File | Change |
|------|--------|
| `patterns/strands-single-agent/basic_agent.py` | Model swap to Nova Pro, multimodal input handling |
| `infra-cdk/lib/backend-stack.ts` | Video upload S3 bucket, presigned URL Lambda, API Gateway endpoint, agent permissions |
| `infra-cdk/lambdas/video-presign/index.py` | NEW — Lambda for generating presigned S3 upload URLs |
| `frontend/src/components/chat/types.ts` | Add `videoAttachment` to `Message` interface |
| `frontend/src/components/chat/ChatInput.tsx` | Add video upload button and file preview |
| `frontend/src/components/chat/ChatInterface.tsx` | Video upload flow, presigned URL fetch, pass video to client |
| `frontend/src/lib/agentcore-client/client.ts` | Add `videoS3Uri` to `invoke()` payload |

## Deployment

After implementation:
1. `cdk deploy` — deploys new S3 bucket, Lambda, API Gateway endpoint, updated agent runtime
2. Update `frontend/public/aws-exports.json` with the new `videoUploadApiUrl` output
3. `python scripts/deploy-frontend.py` — deploys updated frontend

## Risks & Considerations

- **Nova Pro video limits**: Up to 1GB via S3 URI, up to 30 minutes of video. Base64 limited to 25MB.
- **S3 CORS**: Must be configured correctly for browser-based PUT uploads.
- **Cost**: Nova Pro video input is priced per frame/token — large videos can be expensive.
- **Streaming compatibility**: Nova Pro streaming events should follow the same Bedrock Converse format that Strands already handles. No parser changes expected.
- **Tool compatibility**: Nova Pro supports tool use, so existing Gateway tools (sample_tool, pdf_search_tool) should continue to work.
- **Model behavior differences**: Nova Pro may respond differently than Claude. System prompt may need tuning.
