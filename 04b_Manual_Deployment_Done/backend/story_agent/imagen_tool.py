import asyncio
import os
import json
import base64
import tempfile
import time
from typing import Dict, Any, List
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from google.adk.tools import BaseTool, ToolContext
from google.cloud import storage
from google.oauth2 import service_account
from dotenv import load_dotenv
import uuid
from datetime import datetime

load_dotenv()


class ImagenTool(BaseTool):
    """
    Custom ADK tool for generating images using Google Vertex AI Imagen.
    Automatically stores images in GCS bucket to avoid MCP token payload issues.
    """
    
    def __init__(self, project_id: str = None, location: str = None):
        super().__init__(
            name="generate_image",
            description="Generate cartoon-style images (bold outlines, vibrant flat colors, minimal background) using Google Vertex AI Imagen with automatic bucket storage"
        )
        
        self._project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT_ID")
        self._location = location or os.getenv("GOOGLE_CLOUD_REGION") or "us-central1"
        self._bucket_name = os.getenv("GENMEDIA_BUCKET")
        
        if not self._project_id:
            raise ValueError("Google Cloud Project ID not configured. Please set GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_PROJECT_ID environment variable.")
        
        if not self._bucket_name:
            print("⚠️  Warning: GENMEDIA_BUCKET not set. Images will be returned as base64 payloads which may cause token issues.")
        
        # Initialize the Gen AI client on Vertex AI. Imagen models have been retired,
        # so images come from a Gemini image model, served from the global endpoint.
        self._client = genai.Client(
            vertexai=True,
            project=self._project_id,
            location=os.getenv("IMAGE_MODEL_LOCATION", "global"),
        )
        self._model_name = os.getenv("IMAGEN_MODEL", "gemini-3.1-flash-image")
        
        # Initialize GCS client if bucket is configured
        self._storage_client = None
        if self._bucket_name:
            try:
                creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
                if creds_path and os.path.exists(creds_path):
                    credentials = service_account.Credentials.from_service_account_file(creds_path)
                    self._storage_client = storage.Client(credentials=credentials, project=self._project_id)
                else:
                    # Try default credentials
                    self._storage_client = storage.Client(project=self._project_id)
                print(f"✅ GCS client initialized for bucket: {self._bucket_name}")
            except Exception as e:
                print(f"⚠️  Failed to initialize GCS client: {e}")
                self._storage_client = None

    def _generate_image_bytes(self, prompt: str, number_of_images: int, aspect_ratio: str) -> List[bytes]:
        """Call the Gemini image model and return the raw bytes of each generated image."""
        images: List[bytes] = []
        # Gemini image models return one image per request
        for _ in range(number_of_images):
            images.append(self._generate_one_image(prompt, aspect_ratio))
        return images

    def _generate_one_image(self, prompt: str, aspect_ratio: str, max_attempts: int = 6) -> bytes:
        """Request a single image, retrying on rate limits and on responses without an image."""
        last_error = "no image returned"
        for attempt in range(1, max_attempts + 1):
            try:
                response = self._client.models.generate_content(
                    model=self._model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"],
                        image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
                    ),
                )
                for candidate in response.candidates or []:
                    for part in (candidate.content.parts if candidate.content else None) or []:
                        if part.inline_data and part.inline_data.data:
                            return part.inline_data.data
                # No image: record why (e.g. a safety block) and try again
                reasons = [str(c.finish_reason) for c in response.candidates or []]
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    reasons.append(f"prompt blocked: {response.prompt_feedback.block_reason}")
                last_error = f"no image returned ({', '.join(reasons) or 'empty response'})"
            except genai_errors.APIError as e:
                # Only rate limits and server errors are worth retrying
                if e.code != 429 and (e.code or 0) < 500:
                    raise
                last_error = f"{e.code} {e.status}: {e.message}"
            if attempt < max_attempts:
                delay = min(5 * 2 ** (attempt - 1), 30)  # 5s, 10s, 20s, 30s, 30s
                print(f"⚠️ Image attempt {attempt} failed ({last_error}); retrying in {delay}s")
                time.sleep(delay)
        raise RuntimeError(f"Image generation failed after {max_attempts} attempts: {last_error}")

    def get_json_schema(self) -> Dict[str, Any]:
        """Return the JSON schema for this tool's parameters."""
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed text description of the image to generate"
                },
                "negative_prompt": {
                    "type": "string", 
                    "description": "What to avoid in the image (optional)",
                    "default": "photorealistic, realistic, blurry, low quality, watermark, text overlay"
                },
                "aspect_ratio": {
                    "type": "string",
                    "description": "Image aspect ratio",
                    "enum": ["1:1", "9:16", "16:9", "4:3", "3:4"],
                    "default": "16:9"
                },
                "number_of_images": {
                    "type": "integer",
                    "description": "Number of images to generate (1-4)",
                    "minimum": 1,
                    "maximum": 4,
                    "default": 1
                }
            },
            "required": ["prompt"]
        }
    
    async def run(self, ctx: ToolContext, **kwargs) -> str:
        """Generate an image using Vertex AI Imagen and store in GCS bucket."""
        try:
            prompt = kwargs.get("prompt", "")
            negative_prompt = kwargs.get(
                "negative_prompt",
                "photorealistic, realistic, blurry, low quality, watermark, text overlay"
            )
            aspect_ratio = kwargs.get("aspect_ratio", "16:9")
            number_of_images = kwargs.get("number_of_images", 1)
            
            if not prompt.strip():
                return json.dumps({
                    "error": "Prompt is required for image generation"
                })
            
            # Apply strict cartoon style prefix
            style_prefix = (
                "Children's book illustration in cartoon style with bright vibrant colors, simple shapes, and friendly characters. "
            )
            full_prompt = f"{style_prefix} {prompt}".strip()
            
            print(f"🎨 Generating image with prompt: {full_prompt}")
            
            # Run the blocking API call in a thread so the WebSocket stays responsive
            images = await asyncio.to_thread(
                self._generate_image_bytes, full_prompt, number_of_images, aspect_ratio
            )

            image_results = []

            for i, image_bytes in enumerate(images):
                try:
                    # Save to temporary file first
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                        temp_file.write(image_bytes)
                        temp_file.flush()

                        # If bucket is configured, upload to GCS
                        if self._storage_client and self._bucket_name:
                            try:
                                gcs_url = self._upload_to_bucket(temp_file.name, full_prompt, i)
                                image_results.append({
                                    "index": i,
                                    "gcs_url": gcs_url,
                                    "format": "png",
                                    "stored_in_bucket": True
                                })
                                print(f"✅ Image {i} uploaded to GCS: {gcs_url}")
                            except Exception as e:
                                print(f"❌ Failed to upload image {i} to bucket: {e}")
                                # Fallback to base64 if bucket upload fails
                                with open(temp_file.name, "rb") as img_file:
                                    img_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                                    image_results.append({
                                        "index": i,
                                        "base64": img_base64,
                                        "format": "png",
                                        "stored_in_bucket": False,
                                        "bucket_error": str(e)
                                    })
                        else:
                            # No bucket configured, return base64
                            with open(temp_file.name, "rb") as img_file:
                                img_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                                image_results.append({
                                    "index": i,
                                    "base64": img_base64,
                                    "format": "png",
                                    "stored_in_bucket": False
                                })
                        
                        # Clean up temporary file
                        os.unlink(temp_file.name)
                        
                except Exception as e:
                    image_results.append({
                        "index": i,
                        "error": f"Failed to process image: {str(e)}"
                    })
            
            # Count successful bucket uploads
            bucket_uploads = sum(1 for result in image_results if result.get("stored_in_bucket", False))
            
            result = {
                "success": True,
                "images_generated": len(image_results),
                "images_in_bucket": bucket_uploads,
                "bucket_name": self._bucket_name if self._storage_client else None,
                "token_safe": bucket_uploads > 0,  # Indicate if we avoided token issues
                "images": image_results
            }
            
            return json.dumps(result)
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": f"Image generation failed: {str(e)}"
            })
    
    def generate_and_store(
        self,
        prompt: str,
        number_of_images: int = 1,
        negative_prompt: str = "photorealistic, realistic, blurry, low quality, watermark, text overlay",
        aspect_ratio: str = "16:9"
    ) -> List[Dict[str, Any]]:
        """Generate images and store them in GCS bucket, returning list of results."""
        
        style_prefix = (
            "Children's book illustration in cartoon style with bright vibrant colors, simple shapes, and friendly characters. "
        )
        full_prompt = f"{style_prefix} {prompt}".strip()
        
        print(f"🎨 Generating {number_of_images} image(s) with prompt: {full_prompt[:100]}...")
        
        try:
            images = self._generate_image_bytes(full_prompt, number_of_images, aspect_ratio)
            print(f"✅ Imagen API returned {len(images)} images")
        except Exception as e:
            print(f"❌ Imagen API failed: {e}")
            return []
        
        if not images:
            print("⚠️ No images returned from Imagen API")
            return []
            
        results: List[Dict[str, Any]] = []
        for i, image_bytes in enumerate(images):
            print(f"📷 Processing image {i+1}/{len(images)}")
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
                    temp_file.write(image_bytes)
                    temp_file.flush()
                    print(f"💾 Saved image {i+1} to temp file: {temp_file.name}")
                    
                    # Always read base64 for fallback
                    with open(temp_file.name, "rb") as img_file:
                        img_base64 = base64.b64encode(img_file.read()).decode('utf-8')
                    
                    if self._storage_client and self._bucket_name:
                        try:
                            gcs_url = self._upload_to_bucket(temp_file.name, full_prompt, i)
                            print(f"☁️ Uploaded image {i+1} to GCS: {gcs_url}")
                            results.append({
                                "index": i,
                                "gcs_url": gcs_url,
                                "base64": img_base64,  # Include base64 as fallback
                                "format": "png",
                                "stored_in_bucket": True
                            })
                        except Exception as e:
                            print(f"❌ GCS upload failed for image {i+1}: {e}")
                            results.append({
                                "index": i,
                                "base64": img_base64,
                                "format": "png",
                                "stored_in_bucket": False,
                                "bucket_error": str(e)
                            })
                    else:
                        print(f"📦 No bucket configured, using base64 for image {i+1}")
                        results.append({
                            "index": i,
                            "base64": img_base64,
                            "format": "png",
                            "stored_in_bucket": False
                        })
                    
                    os.unlink(temp_file.name)
                    print(f"🗑️ Cleaned up temp file for image {i+1}")
                    
            except Exception as e:
                print(f"❌ Failed to process image {i+1}: {e}")
                continue
                
        print(f"✅ Successfully processed {len(results)} out of {len(images)} images")
        return results

    def _upload_to_bucket(self, local_path: str, prompt: str, index: int) -> str:
        """Upload image to GCS bucket and return public URL."""
        if not self._storage_client or not self._bucket_name:
            raise Exception("GCS client or bucket not configured")
        
        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        # Sanitize prompt for filename
        safe_prompt = "".join(c for c in prompt[:30] if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_prompt = safe_prompt.replace(' ', '_')
        
        blob_name = f"generated_images/{timestamp}_{safe_prompt}_{index}_{unique_id}.png"
        
        # Upload to bucket
        bucket = self._storage_client.bucket(self._bucket_name)
        blob = bucket.blob(blob_name)
        
        with open(local_path, 'rb') as image_file:
            blob.upload_from_file(image_file, content_type='image/png')

        # Make the blob publicly readable
        blob.make_public()
        
        # Don't use make_public() with uniform bucket-level access
        # Instead, assume the bucket is already public or use the public URL format
        # Return public HTTPS URL for browser display
        return blob.public_url 