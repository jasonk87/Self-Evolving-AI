import asyncio
import logging
import os
import uuid
import aiohttp
from typing import Dict, Any, List, Optional
from ai_assistant.core.vision_service import VisionService
from ai_assistant.custom_tools.search_tools import google_custom_search
from ai_assistant.llm_interface.gemini_client import invoke_gemini_model_async
from ai_assistant.config import DEFAULT_MODEL, DEEP_RESEARCH_MAX_URLS, DEEP_RESEARCH_TIMEOUT
import ai_assistant.config as config
from ai_assistant.tools.base import ToolBase

logger = logging.getLogger(__name__)

class DeepResearcher(ToolBase):
    """
    A research assistant that performs deep web research by:
    1. Searching Google for relevant URLs.
    2. Browsing and scraping the content of those URLs using VisionService (Playwright).
    3. Analyzing the content using Gemini to extract specific answers.
    4. Synthesizing a comprehensive research summary.
    """

    def __init__(self):
        super().__init__(tool_name="DeepResearcher")
        self.vision_service = VisionService()

    async def _download_image(self, url: str) -> Optional[str]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        content_type = response.headers.get('content-type', '')
                        if 'image' in content_type:
                            data = await response.read()
                            filename = f"{uuid.uuid4()}.jpg"
                            save_dir = os.path.join("static", "scraped_images")
                            os.makedirs(save_dir, exist_ok=True)
                            filepath = os.path.join(save_dir, filename)
                            with open(filepath, "wb") as f:
                                f.write(data)
                            return f"/static/scraped_images/{filename}"
        except Exception as e:
            logger.error(f"Failed to download image {url}: {e}")
        return None

    async def perform_deep_research(self, query: str, max_depth: int = DEEP_RESEARCH_MAX_URLS) -> Dict[str, Any]:
        """
        Executes the deep research workflow.

        Args:
            query (str): The research question or topic.
            max_depth (int): Maximum number of URLs to visit.

        Returns:
            Dict[str, Any]: A dictionary containing the 'summary' and 'sources'.
        """
        ghost_status = "ENABLED" if config.GHOST_MODE else "DISABLED"
        self.emit_status(f"Starting research for '{query}'... (Ghost Mode: {ghost_status})")

        # Step 1: Search
        self.emit_status("Searching Google for relevant sources...")
        search_data = await google_custom_search(query, num_results=max_depth)
        search_results = search_data.get("results", [])
        if not search_results:
            return {
                "summary": "I could not find any search results for your query. Please check your internet connection or try a different query.",
                "sources": []
            }

        urls_to_visit = [result['link'] for result in search_results if result.get('link')]
        sources = []
        findings = []
        collected_images = []

        # Step 2 & 3: Browse and Analyze
        for i, url in enumerate(urls_to_visit):
            self.emit_status(f"Visiting ({i+1}/{len(urls_to_visit)}): {url}")
            try:
                # Scrape text
                page_content = await asyncio.wait_for(
                    self.vision_service.scrape_page_text(url),
                    timeout=DEEP_RESEARCH_TIMEOUT + 10
                )

                if not page_content:
                    logger.warning(f"DeepResearcher: No content retrieved from {url}")
                    continue

                # Scrape images (The Visual Shopper)
                # Attempt to get images for every page to ensure we capture visuals
                try:
                    image_urls = await self.vision_service.scrape_page_images(url, limit=2)
                    for img_url in image_urls:
                        local_path = await self._download_image(img_url)
                        if local_path:
                            collected_images.append(local_path)
                except Exception as e:
                    logger.error(f"DeepResearcher: Failed to scrape images from {url}: {e}")

                # Analyze content
                self.emit_status(f"Analyzing content from {url}...")
                analysis = await self._analyze_page_content(query, url, page_content)

                if analysis.strip().upper() == "SKIP":
                    logger.info(f"DeepResearcher: Skipping irrelevant page {url}")
                    continue

                findings.append(f"Source: {url}\nContent: {analysis}")
                sources.append(url)

            except asyncio.TimeoutError:
                logger.warning(f"DeepResearcher: Timeout visiting {url}")
                self.emit_status(f"Timeout visiting {url}")
            except Exception as e:
                logger.error(f"DeepResearcher: Error visiting {url}: {e}")
                self.emit_status(f"Error visiting {url}: {e}")

        # Step 4: Synthesize
        if not findings:
            return {
                "summary": "I visited the top search results but could not extract relevant information found to answer your specific query. The pages might have been protected, empty, or irrelevant.",
                "sources": sources,
                "images": collected_images
            }

        self.emit_status("Synthesizing final answer from findings...")
        summary = await self._synthesize_findings(query, findings)
        self.emit_status("Research complete.")

        return {
            "summary": summary,
            "sources": sources,
            "images": collected_images
        }

    async def _analyze_page_content(self, query: str, url: str, content: str) -> str:
        """
        Uses LLM to extract relevant information from a single page.
        """
        # Truncate content to avoid token limits (approx 20k chars is safe for Flash)
        truncated_content = content[:20000]

        prompt = (
            f"You are a Research Assistant. I need to answer: '{query}'.\n"
            f"Here is the text content of {url}:\n\n"
            f"{truncated_content}...\n\n"
            f"Extract the specific code examples, API syntax, or facts that directly answer the user's need. "
            f"Focus on technical details if the query is technical.\n"
            f"If this page contains NO relevant information to the query, answer exactly 'SKIP'."
        )

        response = await invoke_gemini_model_async(
            prompt=f"Analyzing content from {url}\n\n{prompt}",
            model_name=DEFAULT_MODEL,
            task_name="deep_research_analyze_page"
        )
        return response.strip()

    async def _synthesize_findings(self, query: str, findings: List[str]) -> str:
        """
        Synthesizes findings from multiple sources into a final answer.
        """
        combined_findings = "\n\n".join(findings)

        prompt = (
            f"You are a Senior Technical Researcher. The user asked: '{query}'.\n"
            f"Here are the findings from deep web research:\n\n"
            f"{combined_findings}\n\n"
            f"Synthesize these findings into a comprehensive, clear, and accurate answer. "
            f"Prioritize code examples and concrete facts. "
            f"Cite the sources (URLs) where appropriate in the text."
        )

        response = await invoke_gemini_model_async(
            prompt=f"Synthesizing research findings\n\n{prompt}",
            model_name=DEFAULT_MODEL,
            task_name="deep_research_synthesis"
        )
        return response.strip()
