from __future__ import annotations

import io
import os
import re

import torch
from fastapi import FastAPI, Request
from PIL import Image
from transformers import AutoModel, AutoProcessor, AutoTokenizer

MODEL_NAME = "locate-anything-3b"
HF_MODEL_PATH = "nvidia/LocateAnything-3B"

app = FastAPI()

model = None
processor = None
tokenizer = None


@app.on_event("startup")
def load_model() -> None:
    global model, processor, tokenizer
    cuda_ok = torch.cuda.is_available()
    device_name = torch.cuda.get_device_name(0) if cuda_ok else "CPU"
    print(f"[locate_worker] torch={torch.__version__} cuda_available={cuda_ok} "
          f"device={device_name}", flush=True)
    if not cuda_ok:
        raise RuntimeError("CUDA not available; refusing CPU fallback")
    
    tokenizer = AutoTokenizer.from_pretrained(HF_MODEL_PATH, trust_remote_code=True)
    processor = AutoProcessor.from_pretrained(HF_MODEL_PATH, trust_remote_code=True)
    model = AutoModel.from_pretrained(HF_MODEL_PATH, torch_dtype=torch.float16, trust_remote_code=True).to("cuda").eval()
    
    print(f"[locate_worker] {MODEL_NAME} loaded on {device_name} in FP16", flush=True)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "cuda": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
        "model": MODEL_NAME,
    }


@app.post("/detect")
async def detect(request: Request) -> dict:
    try:
        raw = await request.body()
    except Exception as exc:  # noqa: BLE001
        return {
            "person_count": 0,
            "detections": [],
            "model": MODEL_NAME,
            "error": f"body read failed: {exc}",
        }

    if not raw:
        return {
            "person_count": 0,
            "detections": [],
            "model": MODEL_NAME,
            "error": "empty body",
        }

    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        return {
            "person_count": 0,
            "detections": [],
            "model": MODEL_NAME,
            "error": f"decode failed: {exc}",
        }

    try:
        image_width, image_height = image.size
        cats = "</c>".join(["person"])
        prompt = f"Locate all the instances that matches the following description: {cats}."
        
        messages = [
            {"role": "user", "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": prompt},
            ]}
        ]

        text = processor.py_apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        images, videos = processor.process_vision_info(messages)
        inputs = processor(
            text=[text], images=images, videos=videos, return_tensors="pt"
        ).to("cuda")

        pixel_values = inputs["pixel_values"].to(torch.float16)
        input_ids = inputs["input_ids"]
        image_grid_hws = inputs.get("image_grid_hws", None)

        with torch.inference_mode():
            response = model.generate(
                pixel_values=pixel_values,
                input_ids=input_ids,
                attention_mask=inputs["attention_mask"],
                image_grid_hws=image_grid_hws,
                tokenizer=tokenizer,
                max_new_tokens=2048,
                use_cache=True,
                generation_mode="hybrid",
                temperature=0.7,
                do_sample=True,
                top_p=0.9,
                repetition_penalty=1.1,
                verbose=False,
            )

        answer = response[0] if isinstance(response, tuple) else response
        if isinstance(answer, torch.Tensor):
            answer = processor.decode(answer[0], skip_special_tokens=True)
            
        persons = []
        for m in re.finditer(r"<box><(\d+)><(\d+)><(\d+)><(\d+)></box>", answer):
            x1, y1, x2, y2 = [int(g) for g in m.groups()]
            persons.append({
                "bbox": [
                    float(x1 / 1000 * image_width),
                    float(y1 / 1000 * image_height),
                    float(x2 / 1000 * image_width),
                    float(y2 / 1000 * image_height),
                ],
                "confidence": 1.0,  # Confidence not provided by LocateAnything
            })
            
    except Exception as exc:  # noqa: BLE001
        return {
            "person_count": 0,
            "detections": [],
            "model": MODEL_NAME,
            "error": f"inference failed: {exc}",
        }

    return {
        "person_count": len(persons),
        "detections": persons,
        "model": MODEL_NAME,
    }
