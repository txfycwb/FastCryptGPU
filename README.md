# FastCryptGPU: Fast Privacy Preserving Neural Network on GPU with Maximum Pooling


## Introduction
FastCryptGPU is a GPU-based system for privacy-preserving machine learning based on secure multi-party computation (MPC). The system is efficient and supports  end-to-end training/inference on the GPU with Maximum Pooling.

While the latest GPU-based framework CryptGPU accelerates the linear computation of secure neural networks, it is non-linear computationally inefficient and does not support maximum pooling. FastCryptGPU propose a hierarchical design scheme for a GPU-based secure neural network that is more efficient and supports maximum pooling. First, we implement an arithmetically optimized activation layer. The scheme avoids Boolean circuit calculation and runs entirely on arithmetic secret sharing. It effectively reduces communication costs and makes better use of GPU computing performance. Then, we implement a safe and efficient forward propagation and backpropagation of linear layer, activation function, maximum pooling, and loss function. 

Figure 1: System Architecture
<img src="Architecture.png"/>

The base architecture of our system is adapted from [CryptGPU](https://github.com/jeffreysijuntan/CryptGPU) and [CrypTen](https://github.com/facebookresearch/crypten). Our system is based on CryptGPU, which is mainly based on CrypTen.

[CryptGPU](https://github.com/jeffreysijuntan/CryptGPU) is a system for privacy-preserving machine learning based MPC. The implementation is according to the paper: [CryptGPU: Fast Privacy-Preserving Machine Learning on the GPU](https://arxiv.org/abs/2104.10949) by Sijun Tan, Brian Knott, Yuan Tian, David J. Wu.

[CrypTen](https://github.com/facebookresearch/crypten) is a privacy-preserving machine learning framework (PPML) built on top of PyTorch, which aims to make secure computing techniques easily accessible to machine learning practitioners. 

**WARNING**: This code is a prototype for academic performance testing. The code has not received careful code review, and is NOT ready for production use. 


Copyright (C) 2022, [STCS & CGCL](http://grid.hust.edu.cn/) and [Huazhong University of Science and Technology](https://www.hust.edu.cn/).
## Installation

**Building dependencies through pip**

Before installation, please ensure that the python version is above python3.7, and the NVIDIA drivers and CUDA are installed.

Enter the FastCryptGPU directory and install dependencies through `pip3`, then, our system can be installed through `setup.py`.

For simplicity, you can install dependencies in your conda environment.

```bash
git clone https://github.com/FastCryptGPU
cd FastCryptGPU
pip3 install -r requirements.txt
python3 setup.py install
```

## Usage

- **Quick Start**

  The following code shows how the FastCryptgpu code is encrypted and decrypted, and how to do addition, multiplication, and symbol bit acquisition.

  ```python
  import torch
  import crypten

  crypten.init()

  # How to encrypt and decrypt
  x = torch.tensor([1.0, 2.0, 3.0], device='cuda')# if device='cpu', computing runs on the CPU
  x_enc = crypten.cryptensor(x) # encrypt
  x_dec = x_enc.get_plain_text() # decrypt

  # How to do secret addition
  y_enc = crypten.cryptensor([2.0, 3.0, 4.0], device='cuda')
  sum_xy = x_enc + y_enc # add encrypted tensors
  sum_xy_dec = sum_xy.get_plain_text() # decrypt sum

  # How to do secret multiplication
  mul_xy = x_enc *+* y_enc # add encrypted tensors
  mul_xy_dec = mul_xy.get_plain_text() # decrypt sum

  # How to do secret multiplication
  from crypten.mpc.primitives.converters import get_msb # Symbol bit acquisition API of CryptGPU
  from crypten.mpc.gw_relu_helper import gw_get_msb # Symbol bit acquisition API of FastCryptgpu
  sign = get_msb(x_enc._tensor) # Obtaining symbol bit based on ABY3 principle
  sign = gw_get_msb(x_enc._tensor) # Obtaining symbol bit based on Falcon principle
  ```

- **Performance Benchmark**

  To measure the performance, run `scripts/benchmark.py`

  ```bash
  python3 scripts/benchmark.py --exp inference_all
  ```
  where `--exp` specifies the experiments to run. Experiments include `inference_all, train_all, train_plaintext, inference_plaintext`. This line of command will create three processes locally to simulate 3PC computations.

## Distributed Performance Benchmark

The system can perform distributed experiments on three servers, we provide scripts to facilitate this process. The experiment needs to ensure that the three servers can be connected through `LAN`.

First, set the running parameters such as `cuda_visible_devices` in `launch_distributed.sh`. Then, set `username`, `password`, `hostnames` and `master_ip_address` in distributed_launcher.py. Among them, `hostnames` is set to the public IP of the three servers, and `master_ip_address` is set to the private IP of the main server. And install our system on three servers separately. Finally, execute
```
bash launch_distributed.sh
```

## Overall Structure
Next, we introduce the relationship between FastCryptGPU and CryptGPU. It will be introduced from the software directory, mainly explaining what files FastCryptGPU has specifically modified or added to CryptGPU.
```shell
    FastCryptGPU
    └- crypten # Based on the open-source library CrypTen, so keep the original name
       └- commom # Modified tool package: only fix the bug generated by random number in GPU
       └- communicator # Unmodified communication package: Implement (distributed) multi-process communication
       └- cuda # Unmodified GPU package: Adapt to PyTorch CUDA API
       └- debug # Unmodified debug package
       └- nn # Unmodified neural networks package
       └- optim # Unmodified optimizer package
       └- mpc # Modified MPC algorithm package
          └- provider # Unmodified information sources package
          └- primitives # Modified MPC basic operator package
             └- arithmetic.py # Modify the mapping of maxpool and  most significant bit(MSB) in ArithmeticSharedTensor
             └- ...
          └- maxpool # Added maxpool implementation package
             └- maxpool_relu.py # Decomposition implementation and de-pooling of maxpool
             └- ...
          └- mpc.py # Modify the mapping of maxpool and MSB in MPCSharedTensor
          └- gw_relu_helper.py # Added FALCON-based ReLU
          └- prime_2_publicwrap3_sharing_generator.py # Added files for generating precomputed data in various formats
          └- private_compare_w.py # Added files for FALCON-based MSB helper function
          └- ...
       └- gradients.py # Modify the forward and backpropagation of the activation layer, pooling layer and loss function
       └- ...
    └- scripts
       └- benchmark.py # Modified performance test file
       └- network.py # Modified network structure
       └- ...
    └- ...
```
The implementation of getting the most significant bit is according to the paper: [FALCON: Honest-Majority Maliciously Secure Framework for Private Deep Learning](https://arxiv.org/abs/2004.02229) by Sameer Wagh, Shruti Tople, Fabrice Benhamouda, Eyal Kushilevitz, Prateek Mittal, Tal Rabin.

How to replace the operator principle: Basic operator switching such as addition and multiplication modify arithmetic.py, sign bit acquisition and maxpool switching modify gradients.py



## Comparison with CryptGPU
The base architecture of our system is adapted from [CryptGPU](https://github.com/jeffreysijuntan/CryptGPU). CryptGPU is built on ABY3 and Beaver triples. In contrast, our system is arithmetically optimized based on the Falcon framework at the activation layer. It is completely based on arithmetical secret sharing. Besides that, CryptGPU only supports average pooling in the pooling layer. While our system supports both average pooling and maximum pooling.


## Author
CryptGPU is developed in National Engineering Research Center for Big Data Technology and System, Cluster and Grid Computing Lab, Services Computing Technology and System Lab, School of Computer Science and Technology, Huazhong University of Science and Technology, Wuhan, China by Jialei Guo, Xiaoning Wang and Qiangsheng Hua. For any questions, please contact Jialei Guo (guojialei@hust.edu.cn), Xiaoning Wang (wangxiaoning@hust.edu.cn) and Qiangsheng Hua (qshua@hust.edu.cn).
