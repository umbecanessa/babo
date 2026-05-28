# GX10 deployment (LAN + public workers)



Services on the ASUS Ascent GX10 (or similar homelab box) alongside vLLM.



| Service | Container | Port | Notes |

|---------|-----------|------|--------|

| vLLM (Qwen MoE) | `vllm-dev` | `8000` | User-operated; public `brain.babel.agency` |

| Whisper | `pr-whisper` | `4443` | `~/NLS/whisper-server` |

| Vision | `babo-vision` | `8443` | Public `https://brain.babo.agency:8443` |



Full Railway wiring: [babo-cloud-railway-gx10.md](../../docs/configuration/babo-cloud-railway-gx10.md).



## Vision worker



```bash

cd deploy/gx10

export BABO_VISION_SECRET=your-shared-secret   # match Railway GPU_UPSTREAM_SECRET

docker compose -f docker-compose.vision.yml up -d --build

```



Health:



```bash

curl -s http://127.0.0.1:8443/health -H "X-GPU-Worker-Secret: $BABO_VISION_SECRET"

```



Desktop (LAN ambient vision):



```env

NLS_GPU_WORKER_URL=http://192.168.68.96:8443

NLS_GPU_WORKER_SECRET=your-shared-secret

```



Visual Cortex strategy: `dedicated_vlm_lan`.



## Environment



| Variable | Default | Description |

|----------|---------|-------------|

| `BABO_VISION_SECRET` | (empty) | If set, requires `X-GPU-Worker-Secret` on `/vision/*` |

| `VISION_MODEL` | `moondream2` | `moondream2` or SmolVLM id |

| `VISION_DEVICE` | `cpu` | `cpu` recommended while vLLM uses the GPU |


