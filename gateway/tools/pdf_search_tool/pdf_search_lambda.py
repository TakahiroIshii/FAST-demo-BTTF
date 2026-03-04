# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
PDF Search Tool Lambda for FAST AgentCore Gateway.

This Lambda reads a PDF document from S3, extracts its text content,
and performs keyword-based search to find sections relevant to a user's question.
Designed as a demo tool for querying movie scripts or similar text-heavy PDFs.

DESIGN PATTERN:
Follows the FAST "one tool per Lambda" pattern (see docs/GATEWAY.md).
Uses /tmp for caching the extracted PDF text between warm invocations.
"""

import json
import logging
import os
import re
from typing import Any

import boto3
from pypdf import PdfReader

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Module-level cache for extracted PDF text across warm Lambda invocations.
# This avoids re-downloading and re-parsing the PDF on every request.
_cached_pdf_text: str | None = None
_cached_pdf_key: str | None = None


def extract_text_from_s3_pdf(bucket_name: str, object_key: str) -> str:
    """
    Download a PDF from S3 and extract all text content from it.

    Uses /tmp as a local cache directory for the downloaded PDF file.
    The extracted text is also cached in a module-level variable so that
    subsequent warm invocations skip the download and extraction entirely.

    Args:
        bucket_name (str): The S3 bucket name containing the PDF.
        object_key (str): The S3 object key (path) of the PDF file.

    Returns:
        str: The full extracted text content of the PDF.

    Raises:
        botocore.exceptions.ClientError: If the S3 download fails.
        pypdf.errors.PdfReadError: If the PDF cannot be parsed.
    """
    global _cached_pdf_text, _cached_pdf_key

    # Return cached text if we already extracted this PDF
    cache_identifier = f"{bucket_name}/{object_key}"
    if _cached_pdf_text is not None and _cached_pdf_key == cache_identifier:
        logger.info("Using cached PDF text")
        return _cached_pdf_text

    logger.info(f"Downloading PDF from s3://{bucket_name}/{object_key}")

    # Download PDF to Lambda's /tmp directory
    local_path = "/tmp/document.pdf"
    s3_client = boto3.client("s3")
    s3_client.download_file(
        Bucket=bucket_name,
        Key=object_key,
        Filename=local_path,
    )

    # Extract text from all pages using pypdf
    reader = PdfReader(local_path)
    all_text_parts: list[str] = []
    for page_num, page in enumerate(reader.pages):
        page_text = page.extract_text()
        if page_text:
            all_text_parts.append(f"[Page {page_num + 1}]\n{page_text}")

    full_text = "\n\n".join(all_text_parts)

    # Cache for subsequent warm invocations
    _cached_pdf_text = full_text
    _cached_pdf_key = cache_identifier

    logger.info(
        f"Extracted {len(full_text)} characters from {len(reader.pages)} pages"
    )
    return full_text


def search_text(full_text: str, question: str, max_results: int) -> list[str]:
    """
    Search through extracted PDF text to find sections relevant to a question.

    Splits the text into paragraphs, scores each paragraph by how many
    query keywords it contains (case-insensitive), and returns the top
    scoring paragraphs.

    Args:
        full_text (str): The full extracted text from the PDF.
        question (str): The user's search question or keywords.
        max_results (int): Maximum number of text sections to return.

    Returns:
        list[str]: A list of the most relevant text sections, ordered by
                   relevance (highest keyword match count first).
    """
    # Split the question into individual search keywords, ignoring short words
    # that are unlikely to be meaningful (e.g., "a", "the", "is")
    keywords = [
        word.lower()
        for word in re.split(r"\W+", question)
        if len(word) >= 3
    ]

    if not keywords:
        # If no meaningful keywords found, return the first few paragraphs
        # as a fallback so the user gets some content
        paragraphs = [p.strip() for p in full_text.split("\n\n") if p.strip()]
        return paragraphs[:max_results]

    # Split text into paragraphs for scoring
    paragraphs = [p.strip() for p in full_text.split("\n\n") if p.strip()]

    # Score each paragraph by counting how many keywords appear in it
    scored_paragraphs: list[tuple[int, str]] = []
    for paragraph in paragraphs:
        paragraph_lower = paragraph.lower()
        # Count total keyword occurrences (not just unique matches)
        score = sum(
            paragraph_lower.count(keyword) for keyword in keywords
        )
        if score > 0:
            scored_paragraphs.append((score, paragraph))

    # Sort by score descending (most relevant first)
    scored_paragraphs.sort(key=lambda x: x[0], reverse=True)

    # Return the top results
    results = [paragraph for _score, paragraph in scored_paragraphs[:max_results]]
    return results


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda handler for the PDF search tool.

    Follows the FAST AgentCore Gateway Lambda target pattern:
    - Tool name is extracted from context.client_context.custom['bedrockAgentCoreToolName']
    - Tool arguments are passed directly in the event dict
    - Response uses the {'content': [{'type': 'text', 'text': '...'}]} format

    Required environment variables:
        PDF_BUCKET_NAME: S3 bucket containing the PDF document.
        PDF_OBJECT_KEY: S3 object key of the PDF document.

    Args:
        event (dict[str, Any]): Tool arguments from the gateway. Expected keys:
            - question (str): The search question or keywords.
            - max_results (int, optional): Max sections to return. Defaults to 5.
        context (Any): Lambda context with AgentCore metadata in
            context.client_context.custom.

    Returns:
        dict[str, Any]: Response with 'content' array containing matched text,
            or 'error' string if something went wrong.
    """
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        # Extract tool name from Lambda context (strip gateway target prefix)
        delimiter = "___"
        original_tool_name = context.client_context.custom[
            "bedrockAgentCoreToolName"
        ]
        tool_name = original_tool_name[
            original_tool_name.index(delimiter) + len(delimiter) :
        ]

        logger.info(f"Processing tool: {tool_name}")

        if tool_name != "search_pdf":
            logger.error(f"Unexpected tool name: {tool_name}")
            return {
                "error": f"This Lambda only supports 'search_pdf', received: {tool_name}"
            }

        # Read required environment variables — fail loudly if missing
        bucket_name = os.environ["PDF_BUCKET_NAME"]
        object_key = os.environ["PDF_OBJECT_KEY"]

        # Extract tool arguments from the event
        question: str = event["question"]
        max_results: int = event.get("max_results", 5)

        # Extract text from the PDF (cached across warm invocations)
        full_text = extract_text_from_s3_pdf(
            bucket_name=bucket_name,
            object_key=object_key,
        )

        # Search for relevant sections
        results = search_text(
            full_text=full_text,
            question=question,
            max_results=max_results,
        )

        if not results:
            response_text = (
                f"No relevant sections found for: '{question}'. "
                "Try different keywords or a broader question."
            )
        else:
            # Format results with section numbers for clarity
            formatted_sections = []
            for i, section in enumerate(results, start=1):
                formatted_sections.append(f"--- Result {i} ---\n{section}")
            response_text = (
                f"Found {len(results)} relevant section(s) "
                f"for: '{question}'\n\n"
                + "\n\n".join(formatted_sections)
            )

        return {"content": [{"type": "text", "text": response_text}]}

    except KeyError as e:
        logger.error(f"Missing required field: {e}")
        return {"error": f"Missing required field: {e}"}
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}", exc_info=True)
        return {"error": f"Internal server error: {str(e)}"}
