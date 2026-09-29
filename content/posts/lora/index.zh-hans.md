---
title: LoRA
date: "2025-02-21T21:53:00+08:00"
draft: false
lastmod: "2025-02-21T23:25:52+08:00"
slug: lora
summary: LoRA微调方法学习
categories:
- 论文学习
- 生成式人工智能
tags:
- 大模型微调方法
- LoRA
- Low-Rank Adaptation of Large Language Models
cover: LoRA.png
cid: 59
author: caroline
---

原文：[LoRA: Low-Rank Adaptation of Large Language Models](https://arxiv.org/abs/2106.09685)

## 1 简介

冻结预训练模型的权重，在 Transformer 架构的每一层注入可训练的 秩分解矩阵，从而大大减少了下游任务的可训练参数数量。

术语：

* the input and output dimension size of a Transformer layer: $d_{model}$ 
* the query/key/value/output projection matrices in the self-attention module: $W_q,W_k, W_v, W_o$ 
* the pretrained weight matrix: $W$ ($W_0$)
* accumulated gradient update during adaption: $\Delta W$ 
* the rank of a LoRA module: $r$ 
* Transformer MLP feedforward dimension $d_{ffn} = 4\times d_{model}$ 

问题描述：

* 给定一个由 $\Phi$ 参数化的预训练自回归语言模型 $P_{\Phi}(y|x)$ ，考虑将这个预训练模型调整到下游条件文本生成任务。

* 在完全微调过程中，模型初始化为预训练权重 $\Phi_0$ ，并通过反复遵循梯度以最大化条件语言建模目标，从而更新为 $\Phi_0 + \Delta\Phi$ ：
  $$
  \max_{\Phi}\sum_{(x,y)\in \mathcal Z} \sum_{t=1}^{|y|}\log (P_{\Phi}(y_t|x,y_{<t}))
  $$
  对于完全微调，$|\Phi_0| = |\Delta\Phi|$ ，对于大语言模型会导致存储和部署面临挑战。

* 本方法中，任务特定的参数增量 $\Delta\Phi = \Delta\Phi(\Theta)$ 被进一步编码为一组更小规模的参数，其维度满足 $|\Theta|\ll|\Phi_0|$，寻找 $\Delta\Phi$ 的任务变成了对 $\Theta$ 的优化：
  $$
  \max_{\Theta}\sum_{(x,y)\in \mathcal Z} \sum_{t=1}^{|y|}\log (P_{\Phi + \Delta\Phi(\Theta)}(y_t|x,y_{<t}))
  $$

## 2 方法

* 出发点：**当适应特定任务时，预训练语言模型具有较低的“内在维度”**。

* 假设在 适应过程 中 **权重的更新** 也 **具有较低的“内在秩”**。

* 对于一个预训练的权重矩阵 $W_0\in \mathbb R^{d\times k}$ ，通过 低秩分解 $W_0 + \Delta W = W_0 + BA$ 来约束它的更新，其中 $B\in \mathbb R^{d\times r}, A\in \mathbb R^{r\times k}$ ，且秩 $r\ll \min(d, k)$ 。

* 在训练过程中，$W_0$ 是冻结的，不接受梯度更新，而 A 和 B 包含可训练的参数。

  * 注意： $W_0$ 和 $\Delta W = BA$ 都与相同的输入 $x$ 相乘，它们各自输出向量在坐标上相加，对于 $h = W_0 x$ ，修改后的前向传播结果为：
    $$
    h = W_0 x + \Delta Wx = W_0 x + BAx
    $$

    * 对 $A$ 使用随机高斯初始化

    * 对 $B$ 使用零初始化

  * 将 $\Delta W x$ 扩展为 $\frac{\alpha}{r}$ ，其中 $\alpha$ 是 $r$ 中的一个常数，调节 $\alpha$ 与 Adam 优化中调节学习率大致相同。

* LoRA的秩 $r$ 设置为 预训练权重矩阵的秩，将等同于完全微调。



## 3 将 LoRA 应用于 Transformer

Transformer 架构中：

* self-attention 模块中有 四个权重矩阵：$W_q$, $W_k$, $W_v$, $W_o$
* MLP模块中有两个

将 $W_q$ ($W_k$ 或 $W_v$) 视为一个维度为 $d_{model}\times d_{model}$ 的单一矩阵，即使输出维度通常会被切分为 attention heads。论文中的研究限制在仅适配 attention weights 用于下游任务，并冻结 MLP 模块（因此它们在下游任务不参与训练），以简化和提高参数效率。



## 4 源代码

地址：https://github.com/microsoft/LoRA/blob/main/loralib

LoRALayer：LoRA层的基类

```python
class LoRALayer():
    def __init__(
        self, 
        r: int,  # 秩
        lora_alpha: int,  # 缩放因子
        lora_dropout: float,
        merge_weights: bool,  # 决定在推理时将 LoRA 权重合并到原始权重中
    ):
        self.r = r
        self.lora_alpha = lora_alpha
        # Optional dropout
        if lora_dropout > 0.:
            self.lora_dropout = nn.Dropout(p=lora_dropout)
        else:
            self.lora_dropout = lambda x: x
        # Mark the weight as unmerged
        self.merged = False
        self.merge_weights = merge_weights
```

结合 LoRA 的 Embedding层：

```python
class Embedding(nn.Embedding, LoRALayer):
    # LoRA implemented in a dense layer
    def __init__(
        self,
        num_embeddings: int,  # 词汇表大小
        embedding_dim: int,   # 嵌入向量的维度
        r: int = 0,
        lora_alpha: int = 1,
        merge_weights: bool = True,
        **kwargs  # 其他传递给 nn.Embedding 的参数
    ):
        nn.Embedding.__init__(self, num_embeddings, embedding_dim, **kwargs)
        LoRALayer.__init__(self, r=r, lora_alpha=lora_alpha, lora_dropout=0,
                           merge_weights=merge_weights)
        # Actual trainable parameters
        if r > 0:  # 具有 LoRA 层的 Embedding 层
            # A: r*num_embedding    B: embedding_dim*r
            self.lora_A = nn.Parameter(self.weight.new_zeros((r, num_embeddings)))  
            self.lora_B = nn.Parameter(self.weight.new_zeros((embedding_dim, r)))
            self.scaling = self.lora_alpha / self.r
            # 冻结 预训练权重矩阵
            self.weight.requires_grad = False
        self.reset_parameters()

    def reset_parameters(self):  # 重置嵌入层和LoRA层的参数
        nn.Embedding.reset_parameters(self)
        if hasattr(self, 'lora_A'):
            # initialize A the same way as the default for nn.Linear and B to zero
            nn.init.zeros_(self.lora_A)  # 零初始化
            nn.init.normal_(self.lora_B)  # 初始化为正态分布随机矩阵

    def train(self, mode: bool = True):
        nn.Embedding.train(self, mode)
        if mode:  # 训练模式
            if self.merge_weights and self.merged:  # 权重合并
                # Make sure that the weights are not merged
                if self.r > 0:
                    self.weight.data -= (self.lora_B @ self.lora_A).transpose(0, 1) * self.scaling
                self.merged = False
        else:  # 推理模式
            if self.merge_weights and not self.merged:  # 权重未合并
                # Merge the weights and mark it
                if self.r > 0:
                    self.weight.data += (self.lora_B @ self.lora_A).transpose(0, 1) * self.scaling
                self.merged = True
        
    def forward(self, x: torch.Tensor):
        if self.r > 0 and not self.merged:  # 权重未合并
            result = nn.Embedding.forward(self, x)
            after_A = F.embedding(
                x, self.lora_A.transpose(0, 1), self.padding_idx, self.max_norm,
                self.norm_type, self.scale_grad_by_freq, self.sparse
            )
            # x: 输入数据, (batch_size, sequence_length)
            # lora_A.transpose(0,1): (num_embeddings, r)
            # after_A: 输入x 通过 lora_A 映射后的结果, (batch_size, sequence_length, r)
            result += (after_A @ self.lora_B.transpose(0, 1)) * self.scaling
            # lora_B.transpose(0,1): (r, embedding_dim)
            # result: 矩阵乘法, (batch_size, sequence_length, embedding_dim)
            # scaling: 缩放因子, alpha/r
            return result
        else:
            return nn.Embedding.forward(self, x)
```

