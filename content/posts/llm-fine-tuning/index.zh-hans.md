---
title: LLM Fine-tuning
date: "2025-02-22T10:59:00+08:00"
draft: false
lastmod: "2025-02-23T16:04:30+08:00"
slug: llm-fine-tuning
summary: 李宏毅《生成式AI导论》HW5代码
categories:
- 生成式人工智能
tags:
- 大语言模型微调
- LLM Fine-tuning
cover: LFT.png
cid: 60
author: caroline
---

李宏毅《生成式人工智能导论》第五次作业代码（课程提供了源码）的学习

参考：[李宏毅 生成式人工智能导论 HW5](https://colab.research.google.com/drive/1nB3jwRJVKXSDDNO-pbURrao0N2MpqHl8?usp=sharing) 

## 1 准备阶段

安装必备包：

```python
!pip install bitsandbytes==0.43.0
!pip install datasets==2.10.1
!pip install transformers==4.38.2
!pip install peft==0.9.0
!pip install sentencepiece==0.1.99
!pip install -U accelerate==0.28.0
!pip install colorama==0.4.6
!pip install fsspec==2023.9.2
```

> pytorch 版本是：2.6.0+cu118，cu126版本的pytorch似乎和 bitsandbytes 冲突出现bug。

加载包：

```python
import os
import sys
import argparse
import json
import warnings
import logging
warnings.filterwarnings("ignore")

import torch
import torch.nn as nn
import bitsandbytes as bnb
from datasets import load_dataset, load_from_disk
import transformers, datasets
from peft import PeftModel
from colorama import *

from tqdm import tqdm
from transformers import AutoTokenizer, AutoConfig, AutoModelForCausalLM, BitsAndBytesConfig
from transformers import GenerationConfig
from peft import (
    prepare_model_for_int8_training,
    LoraConfig,
    get_peft_model,
    get_peft_model_state_dict,
    prepare_model_for_kbit_training
)
```

固定随机种子：

```python
seed = 42
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False
torch.manual_seed(seed)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(seed)
```

## 2 重要函数

⭐生成训练资料：

```python
def generate_training_data(data_point):
    """
    目标：将数据点（输入文本和输出文本）转换为模型可以读取的 tokens
    :param data_point: 字典，包含字段 "instruction"(说明), "input"(输入), "output"(输出),均为字符串
    :return: 返回一个字典，包含模型的 输入tokens、用于使模型遵循因果关系的 attention mask，以及对应的输出目标
    示例：如果你构造一个字典 data_point_1，其中包含字段 "instruction", "input", "output"，你可以像这样使用这个函数: formulate_article(data_point_1)
    """
    # 构造完整的提示词
    prompt = f"""\
[INST] <<SYS>>
You are a helpful assistant and good at writing Tang poem. 你是一個樂於助人的助手且擅長寫唐詩。
<</SYS>>

{data_point["instruction"]}
{data_point["input"]}
[/INST]"""

    # 计算输入tokens 的数量
    len_user_prompt_tokens = (
        len(
            tokenizer(
                prompt,
                truncation=True,
                max_length=CUTOFF_LEN+1,
                padding="max_length",
            )["input_ids"]
        )-1
    )

    # 将输入提示词和输出文本转换为 tokens
    full_tokens = tokenizer(
        prompt + " " + data_point["input"] + "</s>",
        truncation=True,
        max_length=CUTOFF_LEN+1,
        padding="max_length",
    )["input_ids"][:-1]  # 去掉结束符</s>的token

    return{
        "input_ids": full_tokens,  # 返回完整输入的tokens
        "labels": [-100] * len_user_prompt_tokens  # 对于输入部分，标签为 -100，表示不计算损失
                  + full_tokens[len_user_prompt_tokens:],  # 对于输出部分，标签为对应的 tokens
        "attention_mask": [1] * (len(full_tokens)),  # attention_mask 标记哪些 token 是有效的
    }
```



⭐将指令输入模型得到输出：

```python
def evaluate(instruction, generation_config, max_len, input="", verbose=True):
    """
    目标：用于根据输入字符串生成模型的输出
    示例：如果指令是 "ABC"，输入是 "DEF"，并且你希望模型的输出不超过 128 token，你可以像这样使用此函数：
    evaluate(instruction="ABC", generation_config=generation_config, max_len=128, input="DEF")

    :param instruction: str,指示模型应该执行的任务描述
    :param generation_config: transformers.GenerationConfig 对象，用于指定与模型推理相关的解码参数
    :param max_len: int, 模型输出的最大长度
    :param input: str, 模型用来解决指令的输入字符串，默认为空字符串 "" (没有输入)
    :param verbose: bool, 是否打印模型的输出，默认为 True

    :return: str, 根据指令和输入生成模型的响应
    """
    prompt = f"""\
[INST] <<SYS>>
You are a helpful assistant and good at writing Tang poem. 你是一個樂於助人的助手且擅長寫唐詩。
<</SYS>>

{instruction}
{input}
[/INST]"""

    # 将提示文本转换为模型所需的数字表示形式 (token ids)
    inputs = tokenizer(prompt, return_tensors="pt")  # 转换为 PyTorch 张量格式
    input_ids = inputs["input_ids"].cuda()

    # 使用模型生成回复
    generation_output = model.generate(
        input_ids=input_ids,  # 输入的 token ids
        generation_config=generation_config,  # 生成配置（例如解码策略）
        return_dict_in_generate=True,  # 返回生成的所有信息（包括 scores）
        output_scores=True,  # 输出生成过程中的每一步的得分
        max_new_tokens=max_len,  # 最大生成的 token 数量
    )

    # 将生成的回复解码并打印
    for s in generation_output.sequences:
        # 解码生成的 token 为字符串
        output = tokenizer.decode(s)
        # 去掉无关的部分并进行清理
        output = output.split("[/INST]")[1].replace("</s>", "").replace("<s>", "").replace("Assistant:", "").replace("Assistant", "").strip()

        if verbose:
            print(output)

    return output
```

## 3 测试微调前的模型

加载模型到显卡：

```python
# 配置模型名字
model_name = "TAIDE-LX-7B-Chat"

# 加载模型到显卡
cache_dir = "./cache"

# 配置4位量化参数
nf4_config = BitsAndBytesConfig(
   load_in_4bit=True,  # 启用4位量化
   bnb_4bit_quant_type="nf4",  # 使用 nf4 量化类型
   bnb_4bit_use_double_quant=True,  # 使用双重量化
   bnb_4bit_compute_dtype=torch.bfloat16  # 设置计算的数据类型为 bfloat16
)

# 从指定的模型名称或路径加载预训练的语言模型
model = AutoModelForCausalLM.from_pretrained(
    model_name,  # 模型名称或路径
    cache_dir=cache_dir,  # 缓存目录
    quantization_config=nf4_config,  # 使用4位量化配置
    low_cpu_mem_usage=True  # 启用低CPU内存使用模式
)

# 创建tokenizer并设置结束符号 (eos_token)
logging.getLogger('transformers').setLevel(logging.ERROR)  # 设置日志级别为ERROR，避免输出过多日志
tokenizer = AutoTokenizer.from_pretrained(
    model_name,  # 模型名称或路径
    add_eos_token=True,  # 自动添加结束符号
    cache_dir=cache_dir,  # 缓存目录
    quantization_config=nf4_config  # 使用4位量化配置
)
tokenizer.pad_token = tokenizer.eos_token  # 设置填充符号为结束符号

# 设置模型推理时需要用到的解码参数
max_len = 128  # 设置模型输出的最大长度
generation_config = GenerationConfig(
    do_sample=True,  # 启用采样生成
    temperature=0.1,  # 设置温度，控制生成的多样性
    num_beams=1,  # 使用单束搜索
    top_p=0.3,  # 设置 nucleus 采样的频率阈值
    no_repeat_ngram_size=3,  # 防止生成重复的 n-gram
    pad_token_id=2,  # 填充 token 的 ID
)
```

测试没有微调前模型：

```python
# demo: 测试的唐诗列表
test_tang_list = ['相見時難別亦難，東風無力百花殘。', '重帷深下莫愁堂，臥後清宵細細長。', '芳辰追逸趣，禁苑信多奇。']

# 获得模型在每个示例上的输出
demo_before_finetune = []
for tang in test_tang_list:
    demo_before_finetune.append(f'模型輸入:\n以下是一首唐詩的第一句話，請用你的知識判斷並完成整首詩。{tang}\n\n模型輸出:\n'
                              + evaluate('以下是一首唐詩的第一句話，請用你的知識判斷並完成整首詩。', generation_config, max_len, tang, verbose = False))
    # evaluate 函数用于 根据输入得到模型的输出，返回 str 
    
# 打印输出并将结果存储到文本文件中
for idx in range(len(demo_before_finetune)):
    print(f"示例 {idx + 1}")
    print(demo_before_finetune[idx])
    print("-" * 80)  # 打印分割线
```

## 4 微调阶段

设置训练参数：

```python
"""建议调整这个超参数"""
num_train_data = 2000
# 设置用于训练的数据数量，最大值为5000。在大多数情况下，训练数据越多越好，这样模型可以学习更多样化的诗句，提升生成质量，但也会增加训练时间
# 使用默认参数(1040)：fine-tuning大约需要25分钟，完整跑完所有cell大约需要50分钟
# 使用最大值(5000)：fine-tuning大约需要100分钟，完整跑完所有cell大约需要120分钟

"""可以更改的超参数"""
output_dir = "./output"  # 设置训练结果的输出目录
ckpt_dir = "./exp1"  # 设置模型checkpoint保存的目录（如果希望将模型保存到其他目录，可以修改这里）
num_epoch = 1  # 设置训练的总Epoch数（数字越大，训练越久；如果使用免费的Colab，需要注意训练时间过长可能会导致断线）
LEARNING_RATE = 3e-4  # 设置学习率

cache_dir = "./cache"  # 设置缓存目录路径
from_ckpt = False  # 是否从checkpoint加载模型的权重，默认值为否
ckpt_name = None  # 从特定checkpoint加载权重时使用的文件名，默认值为None
dataset_dir = "./GenAI-Hw5/Tang_training_data.json"  # 设置数据集的目录或文件路径
logging_steps = 20  # 定义训练过程中 每隔多少步 输出一次训练日志
save_steps = 65  # 定义训练过程中 每隔多少步 保存一次模型
save_total_limit = 3  # 控制最多保留多少个模型checkpoint
report_to = None  # 设置实验指标上报的目标，默认值为None
MICRO_BATCH_SIZE = 4  # 定义微批次的大小
BATCH_SIZE = 16  # 定义一个批次的大小
GRADIENT_ACCUMULATION_STEPS = BATCH_SIZE // MICRO_BATCH_SIZE  # 计算每个微批次累积的梯度步数
CUTOFF_LEN = 256  # 设置文本截断的最大长度

LORA_R = 8  # 设置LORA（Layer-wise Random Attention）的R值
LORA_ALPHA = 16  # 设置LORA的Alpha值
LORA_DROPOUT = 0.05  # 设置LORA的Dropout率

VAL_SET_SIZE = 0  # 设置验证集的大小，默认值为0，表示没有验证集
TARGET_MODULES = ["q_proj", "up_proj", "o_proj", "k_proj", "down_proj", "gate_proj", "v_proj"]  # 设置目标模块，这些模块的权重将被保存为checkpoint
device_map = "auto"  # 设置设备映射，默认值为"auto"
world_size = int(os.environ.get("WORLD_SIZE", 1))  # 获取环境变量"WORLD_SIZE"的值，若未设置则默认值为1
ddp = world_size != 1  # 根据world_size判断是否使用分布式数据并行(DDP)，如果world_size为1则不使用DDP
if ddp:
    device_map = {"": int(os.environ.get("LOCAL_RANK") or 0)}  # 分布式训练时，设置每个设备的映射
    GRADIENT_ACCUMULATION_STEPS = GRADIENT_ACCUMULATION_STEPS // world_size  # 分布式训练时，调整梯度累积步数
```

⭐开始微调：

```python
# 创建输出目录
os.makedirs(output_dir, exist_ok=True)
os.makedirs(ckpt_dir, exist_ok=True)

# 根据 from_ckpt 标志，从指定的 checkpoint 加载模型权重
if from_ckpt:
    model = PeftModel.from_pretrained(model, ckpt_name)

# 准备模型以支持 INT8 训练
model = prepare_model_for_int8_training(model)

# 使用 LoraConfig 配置 LORA 模型，设置 LORA 参数
config = LoraConfig(
    r=LORA_R,  # LORA的R值
    lora_alpha=LORA_ALPHA,  # LORA的Alpha值
    target_modules=TARGET_MODULES,  # 目标模块，这些模块的权重将被修改
    lora_dropout=LORA_DROPOUT,  # LORA的Dropout率
    bias="none",  # LORA中是否使用bias
    task_type="CAUSAL_LM",  # 任务类型：因果语言模型
)
model = get_peft_model(model, config)

# 将 tokenizer 的 padding token 设置为 0
tokenizer.pad_token_id = 0

# 加载并处理训练数据
with open(dataset_dir, "r", encoding="utf-8") as f:
    data_json = json.load(f)  # 从数据集文件读取数据
with open("tmp_dataset.json", "w", encoding="utf-8") as f:
    json.dump(data_json[:num_train_data], f, indent=2, ensure_ascii=False)  # 截取前 num_train_data 条数据并保存为新的 JSON 文件

data = load_dataset('json', data_files="tmp_dataset.json", download_mode="force_redownload")

# 将训练数据分为训练集和验证集（如果 VAL_SET_SIZE 大于 0）
if VAL_SET_SIZE > 0:
    train_val = data["train"].train_test_split(
        test_size=VAL_SET_SIZE, shuffle=True, seed=42  # 按比例划分训练集和验证集
    )
    train_data = train_val["train"].shuffle().map(generate_training_data)  # 随机打乱并处理训练数据
    val_data = train_val["test"].shuffle().map(generate_training_data)  # 随机打乱并处理验证数据
else:
    train_data = data['train'].shuffle().map(generate_training_data)  # 仅处理训练数据
    val_data = None  # 如果没有验证集，则设置验证集为 None

# 使用 Transformers 的 Trainer 进行模型训练 ⭐
trainer = transformers.Trainer(
    model=model,  # 模型
    train_dataset=train_data,  # 训练集
    eval_dataset=val_data,  # 验证集
    args=transformers.TrainingArguments(
        per_device_train_batch_size=MICRO_BATCH_SIZE,  # 每个设备的批次大小
        gradient_accumulation_steps=GRADIENT_ACCUMULATION_STEPS,  # 梯度累积步数
        warmup_steps=50,  # 预热步数
        num_train_epochs=num_epoch,  # 训练的总Epoch数
        learning_rate=LEARNING_RATE,  # 学习率
        fp16=True,  # 启用混合精度训练
        logging_steps=logging_steps,  # 每隔多少步记录日志
        save_strategy="steps",  # 保存模型的策略：按步数保存
        save_steps=save_steps,  # 每隔多少步保存一次模型
        output_dir=ckpt_dir,  # 输出目录
        save_total_limit=save_total_limit,  # 最多保留多少个模型checkpoint
        ddp_find_unused_parameters=False if ddp else None,  # 如果使用 DDP，控制梯度更新策略
        report_to=report_to,  # 上报训练过程中的实验指标
    ),
    data_collator=transformers.DataCollatorForLanguageModeling(tokenizer, mlm=False),  # 使用数据整理器处理语言建模数据（不使用掩蔽语言模型）
)

# 禁用模型的 cache 功能，以避免模型训练过程中缓存的内容影响结果
model.config.use_cache = False

# 如果使用 PyTorch 2.0 及以上版本且非 Windows 系统，进行模型编译以加速训练
if torch.__version__ >= "2" and sys.platform != 'win32':
    model = torch.compile(model)

# 开始模型训练
trainer.train()

# 将训练完的模型保存到指定的目录中
model.save_pretrained(ckpt_dir)

# 输出可能的警告信息，指示缺失的权重
print("\n 如果上面有缺失的权重警告信息，请忽略 :)")
```



## 5 测试微调后的模型

首先需要加载我们保存的微调模型检查点。

```python
# 查找所有可用的模型检查点
ckpts = []
for ckpt in os.listdir(ckpt_dir):
    if(ckpt.startswith("checkpoint-")):
        ckpts.append(ckpt) # 将检查点名称添加到列表中

# 对所有检查点进行排序，按检查点编号排序
ckpts = sorted(ckpts, key = lambda ckpt:int(ckpt.split("-")[-1]))
print("所有可用的检查点：")
print(" id: 检查点名称")
for (i, ckpt) in enumerate(ckpts):  # 遍历所有检查点并打印其编号和名称
    print(f"{i:>3}: {ckpt}")
```

可调整的超参数：

```python
""" 你可能需要（但不一定需要）更改检查点 """

id_of_ckpt_to_use = -1  # 要用来进行推理的checkpoint的id（对应上一单元格的输出结果）
                        # 默认值-1指的是上列checkpoints中的"倒数"第一个，也就是最后一个checkpoint
                        # 如果想要选择其他checkpoint，可以把-1改成有列出的checkpoint id中的其中一个

ckpt_name = os.path.join(ckpt_dir, ckpts[id_of_ckpt_to_use])  # 根据选择的checkpoint id，构建完整的checkpoint文件路径

""" 你可能需要（但不一定需要）更改解码参数 """
max_len = 128   # 生成回复的最大长度
temperature = 0.1  # 设置生成回复的随机度，值越小生成的回复越稳定，越大则生成更多样的回复
top_p = 0.3  # Top-p (nucleus) 抽样的概率阈值，用于控制生成回复的多样性
# top_k = 5 # 调整Top-k值，以增加生成回复的多样性和避免生成重复的词汇
```

加载模型：

```python
test_data_path = "GenAI-Hw5/Tang_testing_data.json"  # 设置测试数据的路径
output_path = os.path.join(output_dir, "result.txt")  # 设置输出结果文件的路径

cache_dir = "./cache"
seed = 42
no_repeat_ngram_size = 3  # 设置禁止重复 ngram 的大小，用于避免生成重复的片段

# 配置 BitsAndBytes，设置量化参数
nf4_config = BitsAndBytesConfig(
   load_in_4bit=True,  # 使用 4 位整数来加载模型
   bnb_4bit_quant_type="nf4",  # 设置 4 位量化的类型为 nf4
   bnb_4bit_use_double_quant=True,  # 使用双重量化（双重压缩）来减少模型大小
   bnb_4bit_compute_dtype=torch.bfloat16  # 设置计算时使用的数据类型为 bfloat16
)

# 使用 tokenizer 将模型名称转换成模型可以理解的数字表示形式
tokenizer = AutoTokenizer.from_pretrained(
    model_name,
    cache_dir=cache_dir,
    quantization_config=nf4_config,
)

# 从预训练模型加载并设置为 8 位整型(INT8) 模型
model = AutoModelForCausalLM.from_pretrained(
    model_name,  # 加载预训练的因果语言模型
    quantization_config=nf4_config,  # 设置量化配置
    device_map={'': 0},   # 设置使用的设备，这里指定为 GPU 0
    cache_dir=cache_dir,  # 设置缓存目录
)

# 从指定的 checkpoint 加载模型权重
model = PeftModel.from_pretrained(model, ckpt_name, device_map={'': 0})
```

使用微调模型得到测试数据的测试结果：

```python
result = []  # 用于存储结果的列表

# 设置生成配置，包括随机度、束搜索等相关参数
generation_config = GenerationConfig(
    do_sample=True,  # 启用采样生成
    temperature=temperature,
    num_beams=1,  # 设置束搜索的随机度，值越小越确定
    top_p=top_p,  # 设置 Top-p (nucleus) 采样的概率阈值，用于控制生成文本的多样性
    # top_k=top_k,  # 可选：设置 Top-k 采样的值，以增加生成文本的多样性
    no_repeat_ngram_size=no_repeat_ngram_size,  # 设置禁止重复的 Ngram 大小，避免生成重复片段
    pad_token_id=2  # 设置填充token的ID，通常是2
)

# 读取测试数据
with open(test_data_path, "r", encoding="utf-8") as f:
    test_datas = json.load(f)   # 以 UTF-8编码读取并解析JSON格式的测试数据

# 对每个测试数据进行预测，并保存结果
with open(output_path, "w", encoding="utf-8") as f:
    for(i, test_data) in enumerate(test_datas):
        # 调用 evaluate 函数生成预测结果
        predict = evaluate(test_data["instruction"], generation_config, max_len, test_data["input"], verbose = False)
        # 将结果写入文件并打印
        f.write(f"{i+1}. "+test_data["input"]+predict+"\n")
        print(f"{i+1}. "+test_data["input"]+predict)
```



对比微调前后的测试结果：

```python
# 使用与之前相同的示例进行测试
test_tang_list = ['相見時難別亦難，東風無力百花殘。', '重帷深下莫愁堂，臥後清宵細細長。', '芳辰追逸趣，禁苑信多奇。']

# 对微调后的模型进行推理
demo_after_finetune = []
for tang in test_tang_list:
  # 针对每一首唐诗，输入到微调后的模型中，生成完整的诗歌
  demo_after_finetune.append(f'模型輸入:\n以下是一首唐詩的第一句話，請用你的知識判斷並完成整首詩。{tang}\n\n模型輸出:\n'+evaluate('以下是一首唐詩的第一句話，請用你的知識判斷並完成整首詩。', generation_config, max_len, tang, verbose = False))

# 输出并将结果保存到文本文件
for idx in range(len(demo_after_finetune)):
  print(f"Example {idx + 1}:")  # 打印示例编号
  print(demo_after_finetune[idx])  # 打印模型生成的完整诗歌
  print("-" * 80)  # 输出分隔线
```

