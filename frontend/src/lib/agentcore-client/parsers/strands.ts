// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ChunkParser } from "../types";

/**
 * Parses SSE chunks from Strands agents.
 * Emits typed StreamEvents for text, tool use, messages, and lifecycle.
 *
 * Nova Pro models may emit <thinking>...</thinking> tag markup as part of
 * their chain-of-thought reasoning. The tag markup is stripped from the
 * text stream, but the content inside the tags is preserved so the user
 * always sees a response even if the model wraps its answer in thinking blocks.
 */

/** Buffer that accumulates partial text while we check for tag boundaries. */
let thinkingBuffer = "";

/**
 * Reset the thinking-tag filter state.
 * Call this when starting a new streaming invocation so leftover state
 * from a previous (possibly interrupted) stream doesn't leak.
 */
export function resetThinkingFilter(): void {
  thinkingBuffer = "";
}

/**
 * Strip <thinking>…</thinking> tag markup from a text chunk, but keep
 * the content inside the tags visible.
 *
 * Previous approach suppressed ALL text between the tags, which caused
 * blank responses when Nova Pro wrapped its entire answer in <thinking>
 * blocks (especially when force-stopped before the closing tag arrived).
 *
 * New approach: simply remove the tag markup itself so the user sees the
 * actual content. This is safe because the thinking content is the model's
 * reasoning — it's better to show it than to show nothing.
 *
 * Handles cross-chunk tag splitting by buffering partial tags.
 *
 * @param text - Raw text chunk from the SSE stream
 * @returns The text with <thinking> / </thinking> tags removed (content preserved)
 */
function stripThinkingTags(text: string): string {
  // Prepend any leftover buffer from the previous chunk
  const input = thinkingBuffer + text;
  thinkingBuffer = "";

  // Check if the tail of the input could be a partial tag that hasn't
  // fully arrived yet. We need to buffer it until the next chunk.
  // Longest tag is "</thinking>" at 11 chars.
  const maxTagLen = 11;
  let safeEnd = input.length;

  for (let tailLen = 1; tailLen <= Math.min(maxTagLen, input.length); tailLen++) {
    const tail = input.slice(input.length - tailLen);
    // Check if this tail could be the start of either tag
    if ("<thinking>".startsWith(tail) || "</thinking>".startsWith(tail)) {
      safeEnd = input.length - tailLen;
      thinkingBuffer = tail;
      break;
    }
  }

  // Strip complete tag occurrences from the safe portion
  const safePortion = input.slice(0, safeEnd);
  return safePortion.replace(/<\/?thinking>/g, "");
}

export const parseStrandsChunk: ChunkParser = (line, callback) => {
  if (!line.startsWith("data: ")) return;

  const data = line.substring(6).trim();
  if (!data) return;

  try {
    const json = JSON.parse(data);

    // Text streaming — filter out <thinking>…</thinking> blocks from Nova Pro
    if (typeof json.data === "string") {
      const filtered = stripThinkingTags(json.data);
      if (filtered) {
        callback({ type: "text", content: filtered });
      }
      return;
    }

    // Tool use streaming
    // When a tool call starts, flush any buffered partial tag text so it
    // isn't lost between the thinking phase and the tool result phase.
    if (json.current_tool_use) {
      resetThinkingFilter();
      const tool = json.current_tool_use;
      // First delta for a tool has empty input — treat as start
      if (json.delta?.toolUse?.input === "") {
        callback({ type: "tool_use_start", toolUseId: tool.toolUseId, name: tool.name });
      } else if (json.delta?.toolUse?.input) {
        callback({ type: "tool_use_delta", toolUseId: tool.toolUseId, input: json.delta.toolUse.input });
      }
      return;
    }

    // Complete message (assistant with toolUse, or user with toolResult)
    if (json.message) {
      const msg = json.message;
      callback({ type: "message", role: msg.role, content: msg.content });

      // Extract tool results from user messages
      if (msg.role === "user" && Array.isArray(msg.content)) {
        for (const block of msg.content) {
          if (block.toolResult) {
            const resultText = block.toolResult.content
              ?.map((c: { text?: string }) => c.text)
              .filter(Boolean)
              .join("") || JSON.stringify(block.toolResult.content);
            callback({ type: "tool_result", toolUseId: block.toolResult.toolUseId, result: resultText });
          }
        }
      }
      return;
    }

    // Final result
    if (json.result) {
      callback({ type: "result", stopReason: typeof json.result === "object" ? json.result.stop_reason : "end_turn" });
      return;
    }

    // Lifecycle events
    // Reset thinking filter buffer on new loop iterations so partial tags
    // from a previous pass don't leak into the next one.
    if (json.init_event_loop || json.start_event_loop || json.start) {
      resetThinkingFilter();
      const event = json.init_event_loop ? "init" : json.start_event_loop ? "start_loop" : "start";
      callback({ type: "lifecycle", event });
      return;
    }
  } catch {
    console.debug("Failed to parse strands event:", data);
  }
};
