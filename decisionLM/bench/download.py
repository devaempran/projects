import os
import sys
from huggingface_hub import hf_hub_download
for repo, f in [("Qwen/Qwen3-8B-GGUF","Qwen3-8B-Q4_K_M.gguf"),("Qwen/Qwen3-8B-GGUF","Qwen3-8B-Q5_K_M.gguf"),
                ("Qwen/Qwen3-8B-GGUF","Qwen3-8B-Q8_0.gguf"),("Qwen/Qwen3-4B-GGUF","Qwen3-4B-Q4_K_M.gguf")]:
    p = hf_hub_download(repo, f, local_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")); print("done", p, flush=True)
