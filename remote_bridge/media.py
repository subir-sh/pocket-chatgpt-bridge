"""Serve only raster images referenced by an authenticated conversation read."""
import base64
from collections import OrderedDict
import hashlib
import io
from pathlib import Path
import re
import threading
from urllib.parse import unquote, urlsplit

from PIL import Image, ImageOps


MARKDOWN_IMAGE = re.compile(r'!\[([^\]\n]*)\]\(\s*(?:<([^>\n]+)>|([^\n]+?))\s*\)')


class MediaStore:
    def __init__(self):
        self.refs = OrderedDict()
        self.cache = OrderedDict()
        self.lock = threading.RLock()

    def add(self, thread_id, reference, *, alt="이미지", cwd=None, inline=False):
        if not isinstance(reference, str) or not reference:
            return None
        reference = reference.strip()
        if reference.startswith("https://"):
            # Public/signed URLs load without forwarding the bridge credential.
            return {"url": reference, "alt": alt, "inline": inline, "remote": True}
        if reference.startswith("file://"):
            reference = unquote(urlsplit(reference).path).lstrip("/")
        if not reference.startswith("data:image/"):
            reference = unquote(reference)
            if re.match(r"^/[A-Za-z]:[/\\]", reference):
                reference = reference[1:]
            path = Path(reference)
            if not path.is_absolute():
                if not cwd:
                    return None
                path = Path(cwd) / path
            reference = str(path.resolve())
        asset_id = hashlib.sha256((thread_id + "\0" + reference).encode()).hexdigest()[:32]
        key = (thread_id, asset_id)
        with self.lock:
            if key not in self.refs:
                self.refs[key] = reference
                if len(self.refs) > 1024:
                    self.refs.popitem(last=False)
            else:
                self.refs.move_to_end(key)
        return {"url": f"/api/chats/{thread_id}/images/{asset_id}", "alt": alt, "inline": inline}

    def markdown(self, thread_id, text, cwd=None):
        images = []
        def replace(match):
            ref = match.group(2) or match.group(3)
            # Optional Markdown title is not part of the image path.
            ref = re.sub(r'\s+["\'][^"\']*["\']$', '', ref)
            image = self.add(thread_id, ref, alt=match.group(1) or "이미지", cwd=cwd, inline=True)
            if image:
                images.append(image)
                return f'![{match.group(1)}](<{image["url"]}>)'
            return match.group(1) or "[이미지 위치를 확인하지 못했어요]"
        # Code examples are text, never permission to read a local file.
        chunks = re.split(r'(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)', text)
        for i in range(0, len(chunks), 2):
            chunks[i] = MARKDOWN_IMAGE.sub(replace, chunks[i])
        return "".join(chunks), images

    def read(self, thread_id, asset_id):
        key = (thread_id, asset_id)
        with self.lock:
            reference = self.refs.get(key)
        if reference is None:
            raise FileNotFoundError("Unknown conversation image")
        if reference.startswith("data:image/"):
            version = None
        else:
            stat = Path(reference).stat()
            if stat.st_size > 40_000_000:
                raise ValueError("Image too large")
            version = (stat.st_mtime_ns, stat.st_size)
        cache_key = (key, version)
        with self.lock:
            if cache_key in self.cache:
                self.cache.move_to_end(cache_key)
                return self.cache[cache_key]
        if reference.startswith("data:image/"):
            header, encoded = reference.split(",", 1)
            if ";base64" not in header or len(encoded) > 55_000_000:
                raise ValueError("Unsupported image encoding")
            source = io.BytesIO(base64.b64decode(encoded, validate=True))
        else:
            source = reference
        # Re-encode raster pixels: no SVG/scripts, file download, or image metadata.
        with Image.open(source) as original:
            if original.format not in {"PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF", "AVIF", "ICO", "PPM"}:
                raise ValueError("Unsupported raster format")
            if original.width * original.height > 64_000_000:
                raise ValueError("Image dimensions too large")
            image = ImageOps.exif_transpose(original)
            image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
            image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            output = io.BytesIO()
            image.save(output, "WEBP", quality=90)
            data = output.getvalue()
        with self.lock:
            self.cache[cache_key] = data
            while len(self.cache) > 16:
                self.cache.popitem(last=False)
        return data
