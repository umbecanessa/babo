# Babo vision worker — CPU by default (GX10 GPU is reserved for vLLM).
#
# Override at runtime: VISION_DEVICE=cuda + nvidia-container-toolkit if you
# accept brief VRAM contention during describe calls.

FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt

ARG VISION_MODEL=moondream2
ENV VISION_MODEL=${VISION_MODEL}
ENV VISION_DEVICE=cpu
ENV HF_HUB_DISABLE_PROMPT=1
RUN python -c "\
from transformers import AutoModelForCausalLM, AutoTokenizer; \
mid='vikhyatk/moondream2'; rev='2024-08-26'; \
AutoTokenizer.from_pretrained(mid, revision=rev, trust_remote_code=True); \
AutoModelForCausalLM.from_pretrained(mid, revision=rev, trust_remote_code=True); \
print('model cached')"

COPY server.py .

ENV VISION_PORT=8443
EXPOSE 8443

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8443"]
