---
title: OCCT + VTK 产生的冲突问题
date: "2026-04-13T23:55:31+08:00"
draft: false
lastmod: "2026-04-13T23:55:31+08:00"
slug: "occt-vtk-conflicts"
categories:
- CAE软件搭建
cid: 76
author: caroline
---

最近尝试，增加 VTK 可视化操作，参考了 ParaView，想增加 OSPRay 的控制功能，于是使用相关函数对 OSPRay 进行设置。独立测试的时候不管怎么样都没有问题，一到了项目环境一致报错，一直调试一直调试，直到 claude 根据我完整的 调用堆栈发现，OCCT 和 VTK 有设置冲突。

```
OCCT 在某时候设置了浮点异常标志，导致后续 OSPRay 崩溃
```

后面强制在程序开始设置

```cpp
_controlfp_s(nullptr, _MCW_EM, _MCW_EM);
```

就没有出现野指针访问中断错误。
