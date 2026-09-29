---
title: VQ-VAE 代码实践
date: "2025-03-25T21:49:00+08:00"
draft: false
lastmod: "2025-03-27T21:12:45+08:00"
slug: "vq-vae-practice"
summary: VQ-VAE 代码实践
categories:
- 生成式人工智能
tags:
- VAE
- VQ-VAE
cid: 69
author: caroline
---

## 1 基础环境

pytorch 环境：

```
 pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

运行设备：单GPU卡（英伟达 4060 8G）

运行环境：win11

## 2 部分 python 包的介绍

* `pytorch-lightning` 包的 `LightningModule` 模块

  抽象出大量样板代码（如设备管理、分布式训练、日志记录等），使得开发者可以专注于模型的核心逻辑。

  是 PyTorch 模型的封装类，用于组织和管理深度学习模型的训练、验证、测试以及推理过程。它的主要目标是将模型逻辑与工程实现分离，使代码更加清晰、可维护且易于扩展。

  通过继承 `LightningModule`，用户可以定义模型的架构、优化器、损失函数、数据处理逻辑以及其他训练相关的组件，而无需手动处理繁琐的细节（如 GPU 分配、分布式训练等）。

* `pytorch-lightning` 包的 `LightningModule` 模块

  `LightningDataModule` 是 `pytorch-lightning` 中用于数据管理的一个模块，旨在简化和标准化数据加载、预处理和拆分的流程。它通过将数据相关的逻辑封装在一个单独的类中，使得模型代码与数据处理逻辑解耦，从而提高了代码的可读性、可维护性和可复用性。

* `torchvision.utils` 

  * `vutils.save_image()` 将 PyTorch `Tensor` 格式的图像保存为 PNG、JPG 等常见图片格式。
  * `vutils.make_grid()` 将多张图像**拼接**成一张网格图，方便可视化。

* `Trainer` 是 `pytorch-lightning` 提供的核心类之一，用于管理深度学习模型的训练、验证、测试等过程。通过 `Trainer`，用户无需手动编写训练循环，而是将大部分复杂的逻辑（如设备管理、分布式训练、日志记录等）交给框架处理。

## 3 代码

参考：https://github.com/AntixK/PyTorch-VAE/tree/master

一共三个python文件：`Test.py`, `DataProcess.py`, `VQ_VAE.py` 

文件目录：

```
├── Test.py
├── DataProcess.py
├── VQ_VAE.py
└── Model
    └── Data
        └── CelebA  # 提前下载好的数据
            ├── identity_CelebA.txt
            ├── img_align_celeba.zip
            ├── list_attr_celeba.txt
            ├── list_bbox_celeba.txt
            ├── list_eval_partition.txt
            ├── list_landmarks_align_celeba.txt
            ├── list_landmarks_celeba.txt
            └── img_align_celeba
```

`VQ_VAE.py` ：VQ-VAE实现的核心组件：

* `BaseVAE` 模块：最基础的 VAE 架构

  ```python
  class BaseVAE(nn.Module):
      def __init__(self):
          super(BaseVAE, self).__init__()
  
      def encode(self, input: Tensor) -> List[Tensor]:
          raise NotImplementedError
  
      def decode(self, input: Tensor) -> Any:
          raise NotImplementedError
  
      def sample(self, batch_size:int, current_device:int, **kwargs) -> Tensor:
          raise NotImplementedError
  
      def generate(self, x:Tensor, **kwargs) -> Tensor:
          raise NotImplementedError
  
      @abstractmethod
      def forward(self, *inputs:Tensor) -> Tensor:
          pass
  
      @abstractmethod
      def loss_function(self, *inputs: Any, **kwargs) -> Tensor:
          pass
  ```

* 残差连接层：

  ```python
  class ResidualLayer(nn.Module):
      def __init__(self,
                   in_channels:int,
                   out_channels:int):
          super(ResidualLayer, self).__init__()
          self.resblock = nn.Sequential(
              nn.Conv2d(in_channels, out_channels, kernel_size=3, padding=1, bias=False),
              nn.ReLU(True),
              nn.Conv2d(out_channels, out_channels, kernel_size=1, bias=False),
          )
  
      def forward(self, input:Tensor) -> Tensor:
          return input + self.resblock(input)
  ```

* 矢量量化：将连续的向量映射到离散空间中

  ```python
  class VectorQuantizer(nn.Module):
      def __init__(self,
                   num_embeddings: int,
                   embedding_dim: int,
                   beta: float = 0.25):
          super(VectorQuantizer, self).__init__()
          self.K = num_embeddings  # codebook 中 embedding 向量 的数量 K
          self.D = embedding_dim  # 每个 embedding 向量 的维度 D
          self.beta = beta  # 损失权重系数 (VQ Loss 中的 commitment loss)
  
          self.embedding = nn.Embedding(self.K, self.D)  # K 个 D维向量
          self.embedding.weight.data.uniform_(-1 / self.K, 1 / self.K)  # 代码本初始化
  
      # forward 方法：将连续的 latents 映射到 离散的 codebook 中，并计算 损失
      def forward(self, latents: Tensor):
          latents = latents.permute(0, 2, 3, 1).contiguous()
          # permute: 对 latents 进行维度重排（交换维度顺序）
          # [B × D × H × W] -> [B × H × W × D]
          # [batch_size, height, width, dim]
          latents_shape = latents.shape
          flat_latents = latents.view(-1, self.D)
          # [BHW, D]
  
          # 计算 欧几里得距离
          dist = torch.sum(flat_latents ** 2, dim=1, keepdim=True) + \
                 torch.sum(self.embedding.weight ** 2, dim=1) - \
                 2 * torch.matmul(flat_latents, self.embedding.weight.t())
          # torch.sum(flat_latents ** 2, dim=1, keepdim=True)
          # dim=1 表示沿着 D 维求和, keepdim=True 保持 dim=1，输出形状为 [BHW, 1]
          # torch.sum(self.embedding.weight ** 2, dim=1) 沿着 D 维度求和，输出 [K]
          # torch.matmul(flat_latents, self.embedding.weight.t()) 输出 [BHW, K]
  
          encoding_inds = torch.argmin(dist, dim=1).unsqueeze(1)
          # [BHW, 1]
  
          # one-hot 编码
          device = latents.device
          encoding_one_hot = torch.zeros(encoding_inds.size(0), self.K, device=device)
          # 全零张量，形状 [BHW × K]
          encoding_one_hot.scatter_(1, encoding_inds, 1)
          # scatter_() 的作用：根据 encoding_inds 指定的位置，将 1 赋值到 encoding_one_hot
          # 语法: tensor.scatter_(dim, index, src) dim=1表示在列方向进行操作（即沿着 K 维度）
          # index=encoding_inds: 每个 BHW 样本对应的最近的 codebook 索引
          # src=1, 填充值为 1
          # [BHW × K]
  
          quantized_latents = torch.matmul(encoding_one_hot, self.embedding.weight)  # [BHW, D]
          quantized_latents = quantized_latents.view(latents_shape)  # [B×H×W×D]
  
          # 计算 VQ 损失
          commitment_loss = F.mse_loss(quantized_latents.detach(), latents)
          embedding_loss = F.mse_loss(quantized_latents, latents.detach())
          # .detach() 停止梯度
          vq_loss = commitment_loss * self.beta + embedding_loss
  
          # 直通估计其 STE
          quantized_latents = latents + (quantized_latents - latents).detach()
          # 通过 STE，保证梯度在反向传播时可以流向 latents，从而使 encoder 继续学习有意义的 latent 表示
          # STE 让 quantized_latents 在反向传播时“看起来”等于 latents，确保 encoder 继续学习。
  
          return quantized_latents.permute(0, 3, 1, 2).contiguous(), vq_loss
  ```

* VQ-VAE模块：

  ```python
  class VQVAE(BaseVAE):
      def __init__(self,
                   in_channels:int,    # 输入图像的通道数 (e.g., RGB 图像为 3)
                   embedding_dim:int,  # VQ 层中每个嵌入向量的维度
                   num_embeddings:int, # VQ 层的嵌入向量个数
                   hidden_dims: List=None, # Encoder 和 Decoder 的隐藏层维度
                   beta:float=0.25,    # VQ 损失中的 commitment loss 权重
                   img_size: int = 64,   # 输入图像大小 (默认 64x64)
                   **kwargs) -> None:
          super(VQVAE, self).__init__()
  
          self.embedding_dim = embedding_dim
          self.num_embeddings = num_embeddings
          self.img_size = img_size
          self.beta = beta
  
          modules = []
          if hidden_dims is None:
              hidden_dims = [128, 256]
  
          # 构建 encoder
          for h_dim in hidden_dims:
              modules.append(
                  nn.Sequential(
                      nn.Conv2d(in_channels, out_channels=h_dim,
                                kernel_size=4, stride=2, padding=1),
                      nn.LeakyReLU(),
                  )
              )
              in_channels = h_dim
  
          modules.append(
              nn.Sequential(
                  nn.Conv2d(in_channels, in_channels,
                             kernel_size=3, stride=1, padding=1),
                  nn.LeakyReLU(),
              )
          )
  
          for _ in range(6):
              modules.append(ResidualLayer(in_channels, in_channels))
  
          modules.append(nn.LeakyReLU())
  
          modules.append(
              nn.Sequential(
                  nn.Conv2d(in_channels, embedding_dim,
                            kernel_size=1, stride=1),
                  nn.LeakyReLU(),
              )
          )
  
          self.encoder = nn.Sequential(*modules)
  
          # 矢量量化
          self.vq_layer = VectorQuantizer(num_embeddings,
                                          embedding_dim,
                                          self.beta)
  
          # 构建 decoder
          modules = []
          modules.append(
              nn.Sequential(
                  nn.Conv2d(embedding_dim,
                            hidden_dims[-1],
                            kernel_size=3,
                            stride=1,
                            padding=1),
                  nn.LeakyReLU(),
              )
          )
  
          for _ in range(6):
              modules.append(ResidualLayer(hidden_dims[-1], hidden_dims[-1]))
  
          modules.append(nn.LeakyReLU())
          hidden_dims.reverse()
  
          for i in range(len(hidden_dims) - 1):
              modules.append(
                  nn.Sequential(
                      nn.ConvTranspose2d(hidden_dims[i],
                                         hidden_dims[i + 1],
                                         kernel_size=4,
                                         stride=2,
                                         padding=1),
                      nn.LeakyReLU(),
                  )
              )
  
          modules.append(
              nn.Sequential(
                  nn.ConvTranspose2d(hidden_dims[-1],
                                     out_channels=3,
                                     kernel_size=4,
                                     stride=2, padding=1),
                  nn.Tanh()  # Tanh 归一化输出到 [-1, 1]
              )
          )
  
          self.decoder = nn.Sequential(*modules)
  
      def encode(self, input:Tensor) -> List[Tensor]:
          result = self.encoder(input)
          return [result]
  
      def decode(self, z:Tensor) -> Tensor:
          result = self.decoder(z)
          return result
  
      def forward(self, inputs:Tensor, **kwargs) -> List[Tensor]:
          encoding = self.encode(inputs)[0]
          quantized_inputs, vq_loss = self.vq_layer(encoding)
          return [self.decode(quantized_inputs), inputs, vq_loss]
  
      def loss_function(self, *args, **kwargs) -> dict:
          recons = args[0]
          input = args[1]
          vq_loss = args[2]
  
          recons_loss = F.mse_loss(recons, input)
          loss = recons_loss + vq_loss
          return {'loss': loss,
              'Reconstruction_Loss': recons_loss,
              'VQ_Loss': vq_loss
          }
  
      def sample(self,
                 num_samples: int,
                 current_device: Union[int, str], **kwargs) -> Tensor:
          raise Warning('VQVAE sampler is not implemented.')
  
      def generate(self, x: Tensor, **kwargs) -> Tensor:
          return self.forward(x)[0]
  ```





数据处理文件 `DataProcess.py` ：

* `MyCelebA` 模块：

  ```python
  class MyCelebA(CelebA):
      def _check_integrity(self) -> bool:
          return True
  ```

* `VAEDataset` 模块：

  ```python
  class VAEDataset(LightningDataModule):
      """
      Pytorch Lightning DataModule for VQ-VAE
  
      Args:
          data_dir: 数据集的根目录
          train_batch_size: 训练期间使用的 批大小
          val_batch_size: 验证期间使用的 批大小
          patch_size: 图像裁剪或调整的尺寸
          num_workers: 创建并行 workers 的数量，来加载数据项
          pin_memory: 是否将数据加载到固定内存中，通常在 GPU 上训练时启用以提高性能。
      """
  
      def __init__(
              self,
              data_path: str,
              train_batch_size: int = 8,
              val_batch_size: int = 8,
              patch_size: Union[int, Sequence[int]] = (256, 256),
              num_workers: int = 0,
              pin_memory: bool = False,
              **kwargs
      ):
          super().__init__()
  
          self.data_dir = data_path
          self.train_batch_size = train_batch_size
          self.val_batch_size = val_batch_size
          self.patch_size = patch_size
          self.num_workers = num_workers
          self.pin_memory = pin_memory
  
      def setup(self, stage: Optional[str] = None) -> None:
          train_transforms = transforms.Compose([
              transforms.RandomHorizontalFlip(),
              transforms.CenterCrop(148),
              transforms.Resize(self.patch_size),
              transforms.ToTensor(),
          ])
  
          val_transforms = transforms.Compose([
              transforms.RandomHorizontalFlip(),
              transforms.CenterCrop(148),
              transforms.Resize(self.patch_size),
              transforms.ToTensor(),
          ])
  
          self.train_dataset = MyCelebA(
              self.data_dir,
              split='train',
              transform=train_transforms,
              download=False,
          )
  
          self.val_dataset = MyCelebA(
              self.data_dir,
              split='test',
              transform=val_transforms,
              download=False,
          )
  
      def train_dataloader(self) -> DataLoader:
          return DataLoader(
              self.train_dataset,
              batch_size=self.train_batch_size,
              num_workers=self.num_workers,
              shuffle=True,
              pin_memory=self.pin_memory,
          )
  
      def val_dataloader(self) -> Union[DataLoader, List[DataLoader]]:
          return DataLoader(
              self.val_dataset,
              batch_size=self.val_batch_size,
              num_workers=self.num_workers,
              shuffle=False,
              pin_memory=self.pin_memory,
          )
  
      def test_dataloader(self) -> Union[DataLoader, List[DataLoader]]:
          return DataLoader(
              self.val_dataset,
              batch_size=144,
              num_workers=self.num_workers,
              shuffle=True,
              pin_memory=self.pin_memory,
          )
  ```



`Test.py` ：实验测试

* 由于是单卡，所以修改了一些参数，同时修改了 `training_step` 里面的 优化器选择等内容。

  总的训练模块 `VAEXperiment` 

  ```python
  class VAEXperiment(pl.LightningModule):
      def __init__(
              self,
              vae_model: BaseVAE,
              params: dict
      ) -> None:
          super(VAEXperiment, self).__init__()
  
          self.model = vae_model
          self.params = params
          self.curr_device = None
          self.hold_graph = False
          self.automatic_optimization = False
          try:
              self.hold_graph = self.params['retrain_first_backpass']
          except:
              pass
  
      def forward(self, input: Tensor, **kwargs) -> Tensor:
          return self.model(input, **kwargs)
  
      def training_step(self, batch, batch_idx):
          real_img, labels = batch
          self.curr_device = real_img.device
  
          # 兼容单个优化器和多个优化器
          optimizers = self.optimizers()
          if isinstance(optimizers, list) or isinstance(optimizers, tuple):
              opt1 = optimizers[0]
              opt2 = optimizers[1] if len(optimizers) > 1 else None
          else:
              opt1 = optimizers  # 只有一个优化器
              opt2 = None
  
          # 训练步骤
          results = self.forward(real_img, labels=labels)
          train_loss = self.model.loss_function(
              *results,
              M_N=self.params['kld_weight'],
              optimizer_idx=0,
              batch_idx=batch_idx
          )
  
          opt1.zero_grad()
          self.manual_backward(train_loss['loss'])
          opt1.step()
  
          # 如果有第二个优化器
          if opt2 is not None:
              train_loss_2 = self.model.loss_function(
                  *results,
                  M_N=self.params['kld_weight'],
                  optimizer_idx=1,
                  batch_idx=batch_idx
              )
              opt2.zero_grad()
              self.manual_backward(train_loss_2['loss'])
              opt2.step()
  
          # 记录日志
          self.log_dict({key: val.item() for key, val in train_loss.items()}, sync_dist=True)
  
          return train_loss['loss']  # Python Lightning 会自动执行反向传播
  
      def validation_step(self, batch, batch_idx, optimizer_idx=0):
          real_img, labels = batch
          self.curr_device = real_img.device
  
          results = self.forward(real_img, labels=labels)
          val_loss = self.model.loss_function(
              *results,
              M_N=1.0,
              optimizer_idx=optimizer_idx,
              batch_idx=batch_idx
          )
  
          self.log_dict({f"val_{key}": val.item() for key, val in val_loss.items()}, sync_dist=True)
  
      def on_validation_end(self) -> None:
          self.sample_images()
  
      def sample_images(self):
          test_input, test_label = next(iter(self.trainer.datamodule.test_dataloader()))
          test_input = test_input.to(self.curr_device)
          test_label = test_label.to(self.curr_device)
  
          # 重构图像
          recons = self.model.generate(test_input, labels=test_label)
          vutils.save_image(  # 保存图像
              recons.data,
              os.path.join(self.logger.log_dir,
                           "Reconstructions",
                           f"assets/recons_{self.logger.name}_Epoch_{self.current_epoch}.png"),
              normalize=True,  # 对像素值进行归一化，使得图像可视化效果更好。
              nrow=12  # 每行显示 12 张图像。
          )
  
          try:
              samples = self.model.sample(
                  144,
                  self.curr_device,
                  labels=test_label
              )
              vutils.save_image(
                  samples.cpu().data,
                  os.path.join(self.logger.log_dir,
                               "Samples",
                               f"assets/{self.logger.name}_Epoch_{self.current_epoch}.png"),
                  normalize=True,
                  nrow=12
              )
          except Warning:
              pass
  
      def configure_optimizers(self):
          optims = []
          scheds = []
  
          # 主优化器
          optimizer = optim.Adam(self.model.parameters(),
                                 lr=self.params['LR'],
                                 weight_decay=self.params['weight_decay'])
          optims.append(optimizer)
  
          # 尝试添加第二个优化器（如果配置了 LR_2）
          if self.params.get('LR_2') is not None:
              submodel_params = getattr(self.model, self.params.get('submodel', ''), None)
              if submodel_params is not None:
                  optimizer2 = optim.Adam(submodel_params.parameters(),
                                          lr=self.params['LR_2'])
                  optims.append(optimizer2)
  
          # 尝试添加学习率调度器
          if self.params.get('scheduler_gamma') is not None:
              scheduler = optim.lr_scheduler.ExponentialLR(optims[0],
                                                           gamma=self.params['scheduler_gamma'])
              scheds.append(scheduler)
  
          # 如果有第二个优化器，检查是否需要第二个调度器
          if len(optims) > 1 and self.params.get('scheduler_gamma_2') is not None:
              scheduler2 = optim.lr_scheduler.ExponentialLR(optims[1],
                                                            gamma=self.params['scheduler_gamma_2'])
              scheds.append(scheduler2)
  
          return (optims, scheds) if scheds else optims
  ```

* 运行：

  ```python
  if __name__ == '__main__':
      vae_models = {'VQVAE':VQVAE}
  
      # 配置
      config = {
          "model_params": {
              "name": "VQVAE",
              "in_channels": 3,
              "embedding_dim": 64,
              "num_embeddings": 512,
              "img_size": 64,
              "beta": 0.25
          },
          "data_params": {
              "data_path": "./Model/Data/",
              "train_batch_size": 64,
              "val_batch_size": 64,
              "patch_size": 64,
              "num_workers": 1
          },
          "exp_params": {
              "LR": 0.005,
              "weight_decay": 0.0,
              "scheduler_gamma": 0.0,
              "kld_weight": 0.00025,
              "manual_seed": 1265
          },
          "trainer_params": {
              "accelerator": "gpu",  # 使用 GPU 加速
              "devices": 1,          # 使用 1 个 GPU
              "max_epochs": 10
          },
          "logging_params": {
              "save_dir": "./Model/logs/",
              "name": "VQVAE"
          }
      }
  
      # 设置日志记录器
      tb_logger = TensorBoardLogger(save_dir=config['logging_params']['save_dir'],
                                    name=config['model_params']['name'])
  
      # 设定随机种子，保证实验可复现
      seed_everything(config['exp_params']['manual_seed'], True)
  
      # 初始化模型
      model = VQVAE(**config['model_params'])
      experiment = VAEXperiment(model, config['exp_params'])
  
      data = VAEDataset(**config["data_params"], pin_memory=True)
      data.setup()
  
      runner = Trainer(
          logger=tb_logger,
          callbacks=[
              LearningRateMonitor(),
              ModelCheckpoint(
                  save_top_k=2,
                  dirpath=os.path.join(tb_logger.log_dir, 'checkpoints'),
                  monitor='val_loss',
                  save_last=True,
              )
          ],
          **config['trainer_params']
      )
  
      # 创建保存图像的文件夹
      Path(f"{tb_logger.log_dir}/Samples").mkdir(exist_ok=True, parents=True)
      Path(f"{tb_logger.log_dir}/Reconstructions").mkdir(exist_ok=True, parents=True)
  
      # 开始训练
      print(f"======= Training {config['model_params']['name']} =======")
      runner.fit(experiment, datamodule=data)
  ```
