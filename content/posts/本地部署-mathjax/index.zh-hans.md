---
title: 本地部署 Mathjax
date: "2024-10-21T10:46:00+08:00"
draft: false
lastmod: "2024-10-23T13:11:43+08:00"
slug: 本地部署-mathjax
summary: 博客搭建问题解决
categories:
- 博客搭建
tags:
- 博客搭建
- latex
- mathjax
cover: LL.png
cid: 37
author: caroline
---

前不久，我使用wordpress搭建博客，遇到latex公式无法显示的问题，无论下载哪个插件，`$公式$`显示的都是有问题的，加上wp不是原生支持md的，于是后面更换博客框架为typecho。
在使用typecho的时候，使用 mathjax 库，参照网站给出的脚本加入到 `header.php` 中，在挂节点的时候latex公式能正常显示，但是正常访问还是没办法加载，于是在网上查找本地部署 mathjax的方法，尝试了很多方法，都失败了，最后在 [这个博客](https://zhaokaifeng.com/4359/)找到了正确的答案。

我把 mathjax的源代码中的 es5 中的文件放入 /usr/plugins/mathjax/文件目录下面，然后在 `header.php` 文件的 `</head>` 前面加入下面的代码

```javascript
<script>
MathJax = {
  tex: {
      inlineMath: [['[latex]', '[/latex]'], ['\\(', '\\)']],
      inlineMath: [['$', '$'], ['\\(', '\\)']],
      displayMath: [['[Latex]', '[/Latex]'], ['$$', '$$'], ['\\[', '\\]']]
  }
};
</script>
<script src="yourpath/usr/plugins/mathjax/tex-chtml.js" id="MathJax-script" async></script>
```

最后latex公式成功显示。
这个行内latex：$W + W_0 = W + AB$

这是段latex：

$$
W + W_0 = W + AB
$$
