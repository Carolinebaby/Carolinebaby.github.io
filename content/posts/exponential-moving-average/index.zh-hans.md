---
title: Exponential Moving Average
date: "2024-11-17T16:39:00+08:00"
draft: false
lastmod: "2024-11-17T16:40:10+08:00"
slug: "exponential-moving-average"
summary: EMA 的学习笔记。
categories:
- 生成式人工智能
tags:
- 计算机视觉
- EMA
- 指数移动平均
- 参数平滑的方法
cover: EMA.png
cid: 52
author: caroline
---

* video：
  * https://www.youtube.com/watch?v=lAq96T8FkTw
  * https://www.youtube.com/watch?v=NxTFlzBjS-4
  * https://www.youtube.com/watch?v=lWzo8CajF5s

* https://zhuanlan.zhihu.com/p/68748778

EMA：Exponential Moving Average，指数移动平均，也称权重移动平均（Weighted Moving Average）。

假设有 n 个数据：$[\theta_1, \theta_2, ..., \theta_n]$ 

计算平均值：$v_t = \beta \cdot v_{t-1} + (1-\beta) \cdot \theta_t$ ，其中 $v_t$ 表示前 $t$ 个数据的平均值，$\beta$ 是加权权重值（一般设为 0.9~0.999），不同的 $\beta$ 值会影响平均值的平滑程度。

>  普通求平均值的方法：$v_t = \cfrac{(n-1)\cdot v_{t-1} + \theta_t}{n}$ ，当 $\beta = \cfrac{n-1}{n}$ 时，两式形式相同。

EMA 计算时，$\cfrac{1}{1-\beta}$ 个时刻之前的数据的平均值的权重会衰减到 $\frac1e$ 。

在深度学习的优化过程中，$\theta_t$ 是 $t$ 时刻的模型权重 weights，$v_t$ 是 $t$ 时刻的影子权重 shadow weights。在梯度下降的过程中，会一直维护着这个影子权重，但是影子权重不会参与训练。基本的假设是，模型权重在最后的n步内，会在实际的最优点处抖动，所以我们取最后n步的平均，能使得模型更加的鲁棒。



**EMA 的偏差修正** 

在实际应用中，如果 $v_0$ 设置为0，会导致早期的 EMA 值偏离真实的加权平均值，所以要对 EMA 计算结果进行修正。

引入偏差修正因子，通过调整公式，抵消初始时刻权重累积不足的影响，修正后的 公式为：$\cfrac{v_t}{1 - \beta^t}$ 

修正后，估计值更接近真实值。但随着时间 $t$ 增大，修正效果减弱。
