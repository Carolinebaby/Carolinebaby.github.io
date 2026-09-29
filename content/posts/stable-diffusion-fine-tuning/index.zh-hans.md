---
title: Stable Diffusion Fine-tuning
date: "2025-02-23T16:03:00+08:00"
draft: false
lastmod: "2025-02-23T16:05:00+08:00"
slug: "stable-diffusion-fine-tuning"
summary: Stable Diffusion 微调代码学习
categories:
- 生成式人工智能
tags:
- Stable Diffusion
- Fine tuning
cover: SDFT.png
cid: 61
author: caroline
---

李宏毅《生成式人工智能导论》第十次作业代码（课程提供了源码）的学习

参考：

* https://blog.csdn.net/a131529/article/details/144342428
* https://colab.research.google.com/drive/1dI_-HVggxyIwDVoreymviwg6ZOvEHiLS?usp=sharing#scrollTo=CnJtiRaRuTFX

## 1 准备工作

安装必备的包：

```python
pip -q install timm==1.0.7
pip -q install fairscale==0.4.13
pip -q install transformers==4.41.2
pip -q install requests==2.32.3
pip -q install accelerate==0.31.0
pip -q install diffusers==0.29.1
pip -q install einop==0.0.1
pip -q install safetensors==0.4.3
pip -q install voluptuous==0.15.1
pip -q install jax==0.4.33
pip -q install peft==0.11.1
pip -q install deepface==0.0.92
pip -q install tensorflow==2.17.0
pip -q install keras==3.2.0
```

导入必备包：

```python
import argparse
import logging
import math
import os
import random
import glob
import shutil
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
import torch.utils.checkpoint
import transformers
from peft import PeftModel

# Python Imaging Library（PIL）图像处理
from PIL import Image

# 图像处理
from torchvision import transforms
from torchvision.utils import save_image

# 显示进度条
from tqdm.auto import tqdm

# Parameter-Efficient Fine-tuning(PEFT)库
from peft import LoraConfig, get_peft_model
from peft.utils import get_peft_model_state_dict

# Hugging Face transformers
from transformers import AutoProcessor, AutoModel, CLIPTextModel, CLIPTokenizer, CLIPModel, CLIPProcessor

# Hugging Face Diffusion Model库
import diffusers
from diffusers import AutoencoderKL, DDPMScheduler, DiffusionPipeline, StableDiffusionPipeline, UNet2DConditionModel
from diffusers.optimization import get_scheduler
from diffusers.utils import convert_state_dict_to_diffusers
from diffusers.training_utils import compute_snr
from diffusers.utils.torch_utils import is_compiled_module
from diffusers.models.modeling_outputs import Transformer2DModelOutput

# 面部检测
from deepface import DeepFace

# OpenCV
import cv2
```

基本参数设置：

```python
project_name = "Brad"
root_dir = os.path.join(os.getcwd(), "Loras")
project_dir = os.path.join(root_dir, project_name)
log_dir = os.path.join(project_dir, "logs")
model_path = os.path.join(project_dir, "logs", "checkpoint-last")
captions_folder = images_folder = os.path.join(root_dir, "Datasets", "Brad") 
prompts_folder = os.path.join(root_dir, "Datasets", "prompts")
output_folder = os.path.join(project_dir, "logs")
inference_path = os.path.join(project_dir, "inference")
os.makedirs(root_dir, exist_ok=True)
os.makedirs(project_dir, exist_ok=True)
os.makedirs(images_folder, exist_ok=True)

# 验证集数据
validation_prompt = "validation_prompt.txt"  # 验证提示文件名称
validation_prompt_path = os.path.join(prompts_folder, validation_prompt)
validation_prompt_num = 3  # 每次验证生成的图像数量
validation_step_ratio = 1  # 验证步数的比例

with open(validation_prompt_path, "r") as f:
    validation_prompts = [line.strip() for line in f.readlines()]  # 读取验证提示文件中的内容并去除空白字符
    
# 图像预处理
resolution = 512
IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".BMP"]
train_transform = transforms.Compose(
    [
        transforms.Resize(resolution, interpolation=transforms.InterpolationMode.BILINEAR),
        transforms.CenterCrop(resolution),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
    ]
)
```

### 定义数据集

```python
class Text2ImageDataset(torch.utils.data.Dataset):
    """
    构建文本到图像的微调数据集
    """
    def __init__(self, images_folder, captions_folder, transform, tokenizer):
        """
        :param images_folder: 图像文件夹路径
        :param captions_folder: 标注文件夹路径
        :param transform: 将原始图像转换为 torch.Tensor
        :param tokenizer: CLIPTokenizer, 将文本标注转为 word ids
        """
        # 初始化图像路径列表，并根据指定的扩展名找到所有图像文件
        self.image_paths = []
        for ext in IMAGE_EXTENSIONS:
            self.image_paths.extend(glob.glob(os.path.join(images_folder, f"*{ext}")))
        self.image_paths = sorted(self.image_paths)
 
        # 加载对应的文本标注，依次读取每个文本文件中的内容
        caption_paths = sorted(glob.glob(os.path.join(captions_folder, "*.txt")))
        captions = []
        for p in caption_paths:
            with open(p, "r", encoding="utf-8") as f:
                captions.append(f.readline().strip())
 
        # 确保图像和文本标注数量一致
        if len(captions) != len(self.image_paths):
            raise ValueError("图像数量与文本标注数量不一致，请检查数据集。")
 
        # 使用 tokenizer 将文本标注转换为 word ids
        inputs = tokenizer(
            captions, max_length=tokenizer.model_max_length, padding="max_length", truncation=True, return_tensors="pt"
        )
        self.input_ids = inputs.input_ids
        self.transform = transform
        
        
    def __getitem__(self, idx):
        img_path = self.image_paths[idx]
        input_id = self.input_ids[idx]
        try:
            # 加载图像并将其转换为 RGB 模式，然后应用数据增强
            image = Image.open(img_path).convert("RGB")
            tensor = self.transform(image)
        except Exception as e:
            print(f"⚠️ 无法加载图像路径: {img_path}, 错误: {e}")
            # 返回一个全零的张量和空的输入 ID 以避免崩溃
            tensor = torch.zeros((3, resolution, resolution))
            input_id = torch.zeros_like(input_id)
        
        return tensor, input_id  # 返回处理后的图像和相应的文本标注
    
    
    def __len__(self):
        return len(self.image_paths)
```

## 2 微调

### 2.1 微调相关函数

**Stable Diffusion**
text-to-image Stable Diffusion 的三个组成部分:

1. **UNet**: Diffusion process
2. **VAE**: pixel space ↔ latent space
3. **Text Encoder**: eg. CLIP(Contrastive Language–Image Pretraining), input text → embeddings(tokens)

![LDM](HW10/assets/modelfigure.png)

⭐ LoRA: 冻结原始模型的参数，在 transformer 架构的权重矩阵基础上引入可训练的低秩矩阵。
对于本实验，选择使用 LoRA方法的模块为 UNet 和 Text Encoder(CLIP)

- U-Net 是 Stable Diffusion 的核心去噪网络，负责生成图像。微调 U-Net 是最常见的方式，因为它直接影响图像生成的质量和风格。
- 微调文本编码器可以帮助模型更好地理解特定领域或定制的文本描述，提高文本和图像之间的匹配度

准备 LoRA Model：

```python
def prepare_lora_model(lora_config, pretrained_model_name_or_path, model_path=None, resume=False, merge_lora=False):
    """
    加载 Stable Diffusion 模型并应用 LoRA 层，支持恢复训练和合并 LoRA 权重。
    
    :param lora_config: LoraConfig, LoRA 配置对象，用于设置 LoRA 层的参数。
    :param pretrained_model_name_or_path: str, 预训练模型的名称或路径（如 Huggingface 上的模型）。
    :param model_path: str, 可选，恢复训练时的模型路径。
    :param resume: bool, 是否恢复训练。如果为 True，需要提供有效的 model_path。
    :param merge_lora: bool, 是否合并 LoRA 权重到基础模型，通常在推理时使用。
    
    :return: tuple, 包含以下模型对象：
        ▪ tokenizer: CLIPTokenizer, 用于将文本标注转换为 token。
        ▪ noise_scheduler: DDPMScheduler, 扩散模型的噪声调度器。
        ▪ unet: UNet2DConditionModel, 用于图像生成的 UNet 模型。
        ▪ vae: AutoencoderKL, 处理图像潜在表示的 VAE 模型。
        ▪ text_encoder: CLIPTextModel, 文本编码器，将文本转换为特征向量。
    """
    
    # 噪声调度器，add noise & denoise
    noise_scheduler = DDPMScheduler.from_pretrained(
        pretrained_model_name_or_path, 
        subfolder="scheduler"
    )
 
    # Tokenizer，text → token
    tokenizer = CLIPTokenizer.from_pretrained(
        pretrained_model_name_or_path, 
        subfolder="tokenizer"
    )
 
    # 文本编码器，the tokenized input → latent space representation
    text_encoder = CLIPTextModel.from_pretrained(
        pretrained_model_name_or_path,
        torch_dtype=weight_dtype,
        subfolder="text_encoder"
    )
 
    # VAE 模型，pixel space ↔ latent space
    vae = AutoencoderKL.from_pretrained(pretrained_model_name_or_path, subfolder="vae")
 
    # UNet 模型，Diffusion Process
    unet = UNet2DConditionModel.from_pretrained(
        pretrained_model_name_or_path,
        torch_dtype=weight_dtype,
        subfolder="unet"
    )
    
    if resume:
        # 如果恢复训练，加载上一次的模型权重
        if model_path is None or not os.path.exists(model_path):
            raise ValueError("当 resume 设置为 True 时，必须提供有效的 model_path")
        
        # 使用 PEFT 方法加载 LoRA 权重
        text_encoder = PeftModel.from_pretrained(text_encoder, os.path.join(model_path, "text_encoder"))
        unet = PeftModel.from_pretrained(unet, os.path.join(model_path, "unet"))
 
        # 确保 UNet 和文本编码器的可训练参数 (requires_grad) 被设置为 True
        for param in unet.parameters():
            param.requires_grad = True
        
        for param in text_encoder.parameters():
            param.requires_grad = True

    else:
        # 如果不是恢复训练，则根据 LoRA 配置初始化 LoRA 层
        text_encoder = get_peft_model(text_encoder, lora_config)
        unet = get_peft_model(unet, lora_config)
    
    if merge_lora:
        # 合并 LoRA 权重到基础模型，仅在推理时调用
        text_encoder = text_encoder.merge_and_unload()
        unet = unet.merge_and_unload()
 
        # 切换为评估模式（推理）
        text_encoder.eval()
        unet.eval()
 
    # 冻结 VAE 参数，不参与训练
    vae.requires_grad_(False)
 
    unet.to(DEVICE, dtype=weight_dtype)
    vae.to(DEVICE, dtype=weight_dtype)
    text_encoder.to(DEVICE, dtype=weight_dtype)
    
    return tokenizer, noise_scheduler, unet, vae, text_encoder
```

优化器：

```python
def prepare_optimizer(unet: UNet2DConditionModel, text_encoder: CLIPTextModel, unet_learning_rate=5e-4, text_encoder_learning_rate=1e-4):
    """
    为 UNet 和 Text Encoder 的可训练参数设置优化器，并指定不同的学习率
    :param unet: UNet2DConditionModel
    :param text_encoder: CLIPTextModel
    :param unet_learning_rate: float
    :param text_encoder_learning_rate: float 
    :return: 优化器 Optimizer
    """
    # 筛选出 UNet 和 Text Encoder 中需要训练的 LoRA 层参数
    unet_lora_layers = [p for p in unet.parameters() if p.requires_grad]
    text_encoder_lora_layers = [p for p in text_encoder.parameters() if p.requires_grad]

    # 将需要训练的参数分组并设置不同的学习率
    trainable_params = [
        {"params": unet_lora_layers, "lr": unet_learning_rate},
        {"params": text_encoder_lora_layers, "lr": text_encoder_learning_rate}
    ]
    
    optimizer = torch.optim.AdamW(trainable_params)
    
    return optimizer
```

`collate_fn`：将多个单独的样本（每个样本包含图像张量和文本编码）合并成一个批次，并返回一个字典，字典中包含两组数据：

- `pixel_values`: 一个包含所有图像数据的批次张量
- `input_ids`: 一个包含所有文本编码的批次张量

这种方法通常用于处理数据加载时的批处理操作，特别是在训练深度学习模型时，确保每个批次的数据可以同时被送入模型进行计算。

```python
def collate_fn(examples):
    """
    将多个单独的样本（每个样本包含图像张量和文本编码）合并成一个批次
    :param examples: 一个列表，包含一个批次的多个样本
    :return: pixel_values, input_ids
    """
    pixel_values = []
    input_ids = []
    
    for tensor, input_id in examples:
        pixel_values.append(tensor)
        input_ids.append(input_id)
    
    pixel_values = torch.stack(pixel_values, dim=0).float()
    input_ids = torch.stack(input_ids, dim=0)
    
    return {"pixel_values": pixel_values, "input_ids": input_ids}
```

### 2.2 相关参数设置

```python
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
# 训练相关参数
train_batch_size = 2  # 训练批次大小，即每次训练中处理的样本数量
weight_dtype = torch.bfloat16  # 权重数据类型，使用 bfloat16 以节省内存并加快计算速度
snr_gamma = 5  # SNR 参数，用于信噪比加权损失的调节系数
 
# 设置随机数种子以确保可重复性
seed = 1126  # 随机数种子
torch.manual_seed(seed)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(seed)
    
# Stable Diffusion LoRA 的微调参数
 
# 优化器参数
unet_learning_rate = 1e-4  # UNet 的学习率，控制 UNet 参数更新的步长
text_encoder_learning_rate = 1e-4  # 文本编码器的学习率，控制文本嵌入层的参数更新步长
 
# 学习率调度器参数
lr_scheduler_name = "cosine_with_restarts"  # 设置学习率调度器为 Cosine annealing with restarts，逐渐减少学习率并定期重启
lr_warmup_steps = 100  # 学习率预热步数，在最初的 100 步中逐渐增加学习率到最大值
max_train_steps = 2000  # 总训练步数，决定了整个训练过程的迭代次数
num_cycles = 3  # Cosine 调度器的周期数量，在训练期间会重复 3 次学习率周期性递减并重启

# 预训练的 Stable Diffusion 模型路径，用于加载模型进行微调
pretrained_model_name_or_path = "stablediffusionapi/cyberrealistic-41"

# LoRA 配置
lora_config = LoraConfig(
    r=32,  # LoRA 的秩，即低秩矩阵的维度，决定了参数调整的自由度
    lora_alpha=16,  # 缩放系数，控制 LoRA 权重对模型的影响
    target_modules=[
        "q_proj", "v_proj", "k_proj", "out_proj",  # 指定 Text encoder 的 LoRA 应用对象（用于调整注意力机制中的投影矩阵）
        "to_k", "to_q", "to_v", "to_out.0"  # 指定 UNet 的 LoRA 应用对象（用于调整 UNet 中的注意力机制）
    ],
    lora_dropout=0  # LoRA dropout 概率，0 表示不使用 dropout
)
```

### 2.3 微调前的准备

```python
# 初始化 tokenizer
tokenizer = CLIPTokenizer.from_pretrained(
    pretrained_model_name_or_path,
    subfolder="tokenizer"
)
 
# 准备数据集
dataset = Text2ImageDataset(
    images_folder=images_folder,
    captions_folder=captions_folder,
    transform=train_transform,
    tokenizer=tokenizer,
)
 
train_dataloader = torch.utils.data.DataLoader(
    dataset,
    shuffle=True,
    collate_fn=collate_fn,  # 之前定义的collate_fn()
    batch_size=train_batch_size,
    num_workers=0,
)

# 准备模型
tokenizer, noise_scheduler, unet, vae, text_encoder = prepare_lora_model(
    lora_config,
    pretrained_model_name_or_path,
    model_path,
    resume=False,
    merge_lora=False
)
 
# 准备优化器
optimizer = prepare_optimizer(
    unet, 
    text_encoder, 
    unet_learning_rate=unet_learning_rate, 
    text_encoder_learning_rate=text_encoder_learning_rate
)
 
# 设置学习率调度器
lr_scheduler = get_scheduler(
    lr_scheduler_name,
    optimizer=optimizer,
    num_warmup_steps=lr_warmup_steps,
    num_training_steps=max_train_steps,
    num_cycles=num_cycles
)
```

### 2.4 微调

**训练流程**：整个训练流程是一个典型的扩散模型微调过程，先通过 VAE 将图像转为潜在表示，再加噪声、通过 UNet 预测去噪，最终计算损失并进行优化。

损失计算公式：

* 标准 MSE 损失：
  $$
  \text{MSE}(y_{\text{true}}, y_{\text{pred}}) = \cfrac1N\sum_{i=1}^N (y_{\text{true}, i} - y_{\text{pred}, i})^2
  $$

* 加权 MSE 损失（使用 信噪比 SNR）:

  1. 计算信噪比：$\text{SNR}(t) = \cfrac{\| \text{true noise} \|^2}{\| \text{model prediction} - \text{true noise} \|^2}$ 
  2. 计算损失：
     * 未使用 `snr_gamma` ：$\text{Loss} = \frac{1}{N} \sum_{i=1}^{N} \text{SNR}(t_i) \cdot (y_{\text{true},i} - y_{\text{pred},i})^2$ 
     * 使用 `snr_gamma` ：$\text{Loss} = \frac{1}{N} \sum_{i=1}^{N} \text{mse\_loss\_weight}(t_i) \cdot (y_{\text{true},i} - y_{\text{pred},i})^2$ 

```python
os.environ["TOKENIZERS_PARALLELISM"] = "false"

# 初始化
global_step = 0
best_face_score = float("inf")

# 进度条显示训练进度
progress_bar = tqdm(
    range(max_train_steps),  # 根据 num_training_steps 设置
    desc="训练步骤"
)

# 训练循环
for epoch in range(math.ceil(max_train_steps / len(train_dataloader))):
    unet.train()
    text_encoder.train()
    
    for step, batch in enumerate(train_dataloader):
        if global_step >= max_train_steps:
            break
        
        # pixel_values -> latent presentation
        # scaling_factor 缩放因子
        latents = vae.encode(batch["pixel_values"].to(DEVICE, dtype=weight_dtype)).latent_dist.sample()
        latents = latents * vae.config.scaling_factor
        
        # add noise
        noise = torch.randn_like(latents)
        timesteps = torch.randint(0, noise_scheduler.config.num_train_timesteps, (latents.shape[0], ), device=DEVICE).long()
        noisy_latents = noise_scheduler.add_noise(latents, noise, timesteps)
        
        # 获取文本的嵌入表示
        # 确保模型和输入数据都在同一个设备上
        text_encoder.to(DEVICE)
        batch["input_ids"] = batch["input_ids"].to(DEVICE)
        
        # 然后进行前向传播
        encoder_hidden_states = text_encoder(batch["input_ids"])[0]
        
        # 计算目标值
        if noise_scheduler.config.prediction_type == "epsilon":
            target = noise  # 预测噪声
        elif noise_scheduler.config.prediction_type == "v_prediction":
            target = noise_scheduler.get_velocity(latents, noise, timesteps)  # 预测速度向量
 
        # UNet 模型预测
        model_pred = unet(noisy_latents, timesteps, encoder_hidden_states)[0]
 
        # 计算损失
        if not snr_gamma:  # 默认情况下，使用均方误差（MSE）损失计算模型预测与目标值之间的差异
            loss = F.mse_loss(model_pred.float(), target.float(), reduction="mean")
        else:
            # 根据信噪比（SNR）加权计算 MSE 损失
            snr = compute_snr(noise_scheduler, timesteps)
            mse_loss_weights = torch.stack([snr, snr_gamma * torch.ones_like(timesteps)], dim=1).min(dim=1)[0]
            if noise_scheduler.config.prediction_type == "epsilon":
                mse_loss_weights = mse_loss_weights / snr
            elif noise_scheduler.config.prediction_type == "v_prediction":
                mse_loss_weights = mse_loss_weights / (snr + 1)
            
            # 计算加权的 MSE 损失
            loss = F.mse_loss(model_pred.float(), target.float(), reduction="none")
            loss = loss.mean(dim=list(range(1, len(loss.shape)))) * mse_loss_weights
            loss = loss.mean()
 
        # 反向传播
        loss.backward()
        optimizer.step()
        lr_scheduler.step()
        optimizer.zero_grad()
        progress_bar.update(1)
        global_step += 1
 
        # 打印训练损失
        if global_step % 100 == 0 or global_step == max_train_steps:
            print(f"🔥 步骤 {global_step}, 损失: {loss.item()}")
 
        # 保存中间检查点，当前简单设置为每 500 步保存一次
        if global_step % 500 == 0:
            save_path = os.path.join(output_folder, f"checkpoint-{global_step}")
            os.makedirs(save_path, exist_ok=True)
 
            # 使用 save_pretrained 保存 PeftModel
            unet.save_pretrained(os.path.join(save_path, "unet"))
            text_encoder.save_pretrained(os.path.join(save_path, "text_encoder"))
            print(f"💾 已保存中间模型到 {save_path}")
 
# 保存最终模型到 checkpoint-last
save_path = os.path.join(output_folder, "checkpoint-last")
os.makedirs(save_path, exist_ok=True)
unet.save_pretrained(os.path.join(save_path, "unet"))
text_encoder.save_pretrained(os.path.join(save_path, "text_encoder"))
print(f"💾 已保存最终模型到 {save_path}")
 
print("🎉 微调完成！")
```

## 3 生成图像评估

加载用于验证的 prompts：

```python
def load_validation_prompts(validation_prompt_path):
    """
    加载验证提示文本
    :param validation_prompt_path: str, 验证提示文件的路径
    :return: validation_prompt: list，验证提示的字符串列表，每一行就是一个 prompt
    """
    with open(validation_prompt_path, "r", encoding="utf-8") as f:
        validation_prompt = [line.strip() for line in f.readlines()]
    return validation_prompt
```

**定义生成图像的函数**

DiffusionPipeline 是从 Hub 加载任何预训练扩散管道进行推理的最快方式。

> You shouldn’t use the DiffusionPipeline class for training or finetuning a diffusion model. Individual components (for example, UNet2DModel and UNet2DConditionModel) of diffusion pipelines are usually trained individually, so we suggest directly working with them instead.

The pipeline type (for example StableDiffusionPipeline) of any diffusion pipeline loaded with from_pretrained() is automatically detected and pipeline components are loaded and passed to the __init__ function of the pipeline.

```python
def generate_images(pipeline, prompts, num_inference_steps=50, guidance_scale=7.5, output_folder="inference", generator=None):
    """
    使用 DiffusionPipeline 生成图像，保存到指定文件夹并返回生成的图像列表
    :param pipeline: DiffusionPipeline
    :param prompts: list, 文本提示列表
    :param num_inference_steps: int, 推理步骤数，越高图像质量越好，但推理时间也会增加
    :param guidance_scale: float, 决定文本提示对生成图像的影响程度
    :param output_folder: str, 保存生成图像的文件夹路径
    :param generator: torch.Generator, 控制生成随机数的种子，确保图像生成的一致性
    :return: 生成的图像列表，同时图像也会保存到指定文件夹
    """
    print("🎨 正在生成图像...")
    os.makedirs(output_folder, exist_ok=True)
    generated_images = []
    
    for i, prompt in enumerate(tqdm(prompts, desc="生成图像中")):
        # 使用 pipeline 生成图像
        image = pipeline(
            prompt, 
            num_inference_steps=num_inference_steps, 
            guidance_scale=guidance_scale, 
            generator=generator
        ).images[0]
        
        # 保存图像到指定文件夹
        save_file = os.path.join(output_folder, f"assets/generated_{i+1}.png")
        image.save(save_file)
        
        # 将图像保存到列表中，稍后返回
        generated_images.append(image)
    
    print(f"✅ 已生成并保存 {len(prompts)} 张图像到 {output_folder}")
    
    return generated_images
```

**定义评估函数**

本实验的目的是“AI 换脸”，有两个新的度量:
* 无脸图像的数量：使用 deepface库 检测生成图像中的人脸，如果没有检测到人脸，则该图像计为无脸图像，数量 + 1
* 面部相似性，使用 deepface库 提取生成图像中人脸特征，然后与训练集中人脸的特征进行对比，通过计算欧氏距离来衡量相似度，距离越小，表示生成的人脸与训练集中人脸的相似度越高。

评估生成图像和文本提示的匹配度：CLIP 评分

```python
def evaluate(lora_config):
    """
    加载模型、生成图像并评估。

    1. 加载验证文本提示（prompts）用于生成图像。
    2. 加载和准备 LoRA 微调后的模型。
    3. 使用 DiffusionPipeline 生成图像。
    4. 评估生成图像的人脸相似度、CLIP 评分和无面部图像数量。
    5. 打印评估结果。
    """
    print("📂 加载验证提示...")
    validation_prompts = load_validation_prompts(validation_prompt_path)

    print("🔧 准备 LoRA 模型...")
    # 准备 LoRA 模型（用于推理，合并权重）
    tokenizer, noise_scheduler, unet, vae, text_encoder = prepare_lora_model(
        lora_config,
        pretrained_model_name_or_path,
        model_path=model_path,
        resume=True,  # 从检查点恢复
        merge_lora=True  # 合并 LoRA 权重
    )

    # 创建 DiffusionPipeline 并更新其组件
    print("🔄 创建 DiffusionPipeline...")
    pipeline = DiffusionPipeline.from_pretrained(
        pretrained_model_name_or_path,
        unet=unet,  # 传递基础模型
        text_encoder=text_encoder,  # 传递基础模型
        torch_dtype=weight_dtype,
        safety_checker=None,
    )
    pipeline = pipeline.to(DEVICE)

    # 加载 CLIP 模型和处理器
    print("🎯 加载 CLIP 模型...")
    clip_model_name = "openai/clip-vit-base-patch32"
    clip_model = CLIPModel.from_pretrained(clip_model_name).to(DEVICE)
    clip_processor = CLIPProcessor.from_pretrained(clip_model_name)

    # CLIP 模型设置为评估模式
    clip_model.eval()

    # 设置随机数种子
    generator = torch.Generator(device=DEVICE)
    generator.manual_seed(seed)

    # 加载训练图像的面部嵌入
    print("📂 加载训练图像的面部嵌入...")
    train_image_paths = sorted([
        p for p in glob.glob(os.path.join(images_folder, "*"))
        if any(p.endswith(ext) for ext in IMAGE_EXTENSIONS)
    ])
    train_emb_list = []
    for img_path in tqdm(train_image_paths, desc="提取训练图像面部嵌入"):
        face_representation = DeepFace.represent(
            img_path,
            detector_backend="ssd",
            model_name="GhostFaceNet",
            enforce_detection=False
        )
        if face_representation:
            embedding = face_representation[0]['embedding']
            train_emb_list.append(embedding)

    if len(train_emb_list) == 0:
        print("⚠️ 未能提取到任何训练图像的面部嵌入。")
        train_emb = torch.tensor([]).to(DEVICE)
    else:
        train_emb = torch.tensor(train_emb_list).to(DEVICE)

    # 生成图像
    generated_images = generate_images(
        pipeline=pipeline,
        prompts=validation_prompts,
        num_inference_steps=30,
        guidance_scale=7.5,
        output_folder=inference_path,
        generator=generator
    )

    # 评估生成的图像，mis记录无法检测到面部的图像数量
    face_score, clip_score, mis = 0, 0, 0  # 初始化评估分数和计数
    valid_emb = []
    print("📊 正在计算评估分数...")

    for i, image in enumerate(tqdm(generated_images, desc="评估图像中")):
        # 使用 DeepFace 检测面部特征
        opencvImage = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        emb = DeepFace.represent(
            opencvImage,
            detector_backend="ssd",
            model_name="GhostFaceNet",
            enforce_detection=False,
        )
        if not emb or emb[0].get('face_confidence', 0) == 0:
            mis += 1  # 无法检测到面部的图像数量
            continue

        # 计算 CLIP 分数
        current_prompt = validation_prompts[i]
        inputs = clip_processor(text=current_prompt, images=image, return_tensors="pt").to(DEVICE)
        with torch.no_grad():
            outputs = clip_model(**inputs)
        sim = outputs.logits_per_image
        clip_score += sim.item()

        # 收集有效的面部嵌入
        valid_emb.append(emb[0]['embedding'])

    # 如果没有有效的面部嵌入，则返回默认分数
    if len(valid_emb) == 0:
        print("⚠️ 无法检测到面部嵌入！")
        return 0, 0, mis

    # 计算面部相似度分数（使用欧氏距离）
    valid_emb = torch.tensor(valid_emb).to(DEVICE)
    valid_emb = valid_emb / valid_emb.norm(p=2, dim=-1, keepdim=True)
    train_emb = train_emb / train_emb.norm(p=2, dim=-1, keepdim=True)
    face_distance = torch.cdist(valid_emb, train_emb, p=2).mean().item()
    face_score = face_distance  # 平均欧氏距离作为面部相似性分数
    clip_score /= (len(validation_prompts) - mis) if (len(validation_prompts) - mis) > 0 else 1
    print("📈 评估完成！")

    # 打印评估结果
    print(f"✅ 面部相似度评分 (平均欧氏距离): {face_score:.4f} (越低越好，表示生成图像与训练图像更相似)")
    print(f"✅ CLIP 评分 (平均相似度): {clip_score:.4f} (越高越好，表示生成图像与文本提示的相关性更强)")
    print(f"✅ 无面部图像数量: {mis} (无法检测到面部的生成图像数量)")


# 调用函数执行
evaluate(lora_config)
```

