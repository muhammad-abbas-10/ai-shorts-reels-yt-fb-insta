from __future__ import annotations

import importlib.util
from pathlib import Path

from PIL import Image, ImageOps

from .visuals import visual_terms


_REPO = "Xenova/clip-vit-base-patch32"
_REVISION = "d15189d7028b43f1d3e65039190477f6af591c2a"
_FILES = ("onnx/vision_model_quantized.onnx", "onnx/text_model_quantized.onnx",
          "tokenizer.json", "tokenizer_config.json", "preprocessor_config.json", "config.json")
_DISTRACTORS = (
    "a bee or insect on flowers",
    "birds and flowers in nature",
    "a forest, mountains, or a scenic landscape",
    "a person using a laptop in an office",
    "a person standing in front of a green screen",
    "a street with cars and buildings",
    "food and drinks on a table",
    "an abstract colorful animated background",
    "a close-up of an animal",
    "a blank computer screen",
)


class LocalVision:
    @staticmethod
    def dependencies_available() -> bool:
        return all(importlib.util.find_spec(name) is not None
                   for name in ("onnxruntime", "tokenizers", "huggingface_hub", "numpy"))

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self._vision = None
        self._text = None
        self._tokenizer = None
        self._text_cache: dict[str, object] = {}

    def _load(self) -> None:
        if self._vision is not None:
            return
        import onnxruntime as ort
        from huggingface_hub import snapshot_download
        from tokenizers import Tokenizer

        if not all((self.model_dir / name).is_file() for name in _FILES):
            snapshot_download(_REPO, revision=_REVISION, local_dir=self.model_dir,
                              allow_patterns=list(_FILES), max_workers=2)
        onnx = self.model_dir / "onnx"
        self._vision = ort.InferenceSession(str(onnx / "vision_model_quantized.onnx"),
                                            providers=["CPUExecutionProvider"])
        self._text = ort.InferenceSession(str(onnx / "text_model_quantized.onnx"),
                                          providers=["CPUExecutionProvider"])
        self._tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))

    def _text_embeddings(self, phrases: tuple[str, ...]):
        import numpy as np

        uncached = [phrase for phrase in phrases if phrase not in self._text_cache]
        if uncached:
            pad_id = self._tokenizer.token_to_id("<|endoftext|>")
            ids = []
            for phrase in uncached:
                tokens = self._tokenizer.encode(phrase).ids[:77]
                ids.append(tokens + [pad_id] * (77 - len(tokens)))
            vectors = self._text.run(None, {"input_ids": np.asarray(ids, dtype=np.int64)})[0]
            vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
            self._text_cache.update(zip(uncached, vectors))
        return np.stack([self._text_cache[phrase] for phrase in phrases])

    def _image_embedding(self, frame: Image.Image):
        import numpy as np

        resized = ImageOps.fit(frame.convert("RGB"), (224, 224), method=Image.Resampling.LANCZOS)
        pixels = np.asarray(resized, dtype=np.float32) / 255
        pixels = (pixels - np.asarray((0.48145466, 0.4578275, 0.40821073))) / \
                 np.asarray((0.26862954, 0.26130258, 0.27577711))
        pixels = np.transpose(pixels, (2, 0, 1))[None].astype(np.float32)
        vector = self._vision.run(None, {"pixel_values": pixels})[0][0]
        return vector / np.linalg.norm(vector)

    @staticmethod
    def _chroma_screen(frame: Image.Image) -> bool:
        import numpy as np

        pixels = np.asarray(frame.resize((120, 213)), dtype=np.uint8).astype(np.int16)
        red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
        bright_green = (green > 145) & (green > red * 1.6) & (green > blue * 1.35)
        return float(bright_green.mean()) > 0.45

    def matches(self, frames: tuple[Image.Image, Image.Image], query: str) -> bool:
        self._load()
        subject_terms = visual_terms(query)
        distractors = tuple(label for label in _DISTRACTORS
                            if not ((visual_terms(label) - {"a", "of"}) & subject_terms))
        positives = (f"a video frame clearly showing {query}",
                     f"a photo or illustration of {query}")
        phrases = positives + distractors
        embeddings = self._text_embeddings(phrases)
        for frame in frames:
            if self._chroma_screen(frame):
                return False
            scores = embeddings @ self._image_embedding(frame)
            positive = float(scores[:len(positives)].max())
            negative = float(scores[len(positives):].max()) if distractors else 0.0
            if positive < 0.21 or positive < negative + 0.015:
                return False
        return True
