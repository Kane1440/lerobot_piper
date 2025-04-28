**开发不易，如果有帮助的话请点个star！**
# 安装环境
使用Anaconda新建一个python3.10环境 [`miniconda`](https://docs.anaconda.com/free/miniconda/index.html):
```bash
conda create -y -n lerobot python=3.10
conda activate lerobot
```

安装 🤗 LeRobot 依赖:
```bash
pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple

pip uninstall numpy
pip install numpy==1.26.0
pip install pynput
```

安装 Piper 机械臂依赖:  
```bash
pip install python-can
pip install piper_sdk
sudo apt update && sudo apt install can-utils ethtool
pip install pygame
```

# 主从臂遥操作

#### **需要先配置`can_config.sh`脚本，以两台piper机械臂为例**

##### 1) 逐个拔插can模块并一一记录每个模块对应的usb口硬件地址

在`can_config.sh`中，`EXPECTED_CAN_COUNT`参数为想要激活的can模块数量，现在假设为2

(1) 然后can模块中的其中一个单独插入PC，执行

```shell
sudo ethtool -i can0 | grep bus
```

并记录下`bus-info`的数值例如`1-2:1.0`

(2) 接着插入下一个can模块，注意**不可以**与上次can模块插入的usb口相同，然后执行

```shell
sudo ethtool -i can1 | grep bus
```

注：**一般第一个插入的can模块会默认是can0，第二个为can1，如果没有查询到can可以使用`bash find_all_can_port.sh`来查看刚才usb地址对应的can名称**

##### 2) 预定义USB 端口、目标接口名称及其比特率

假设上面的操作记录的`bus-info`数值分别为`1-2:1.0`、`1-4:1.0`，则将下面的`USB_PORTS["1-9:1.0"]="can_left:1000000"`的中括号内部的双引号内部的参数换为`1-2:1.0`和`1-4:1.0`.

最终结果为：

`USB_PORTS["1-2:1.0"]="can_left:1000000"`

`USB_PORTS["1-4:1.0"]="can_right:1000000"`

注：**1-2:1.0硬件编码的usb端口插入的can设备，名字被重命名为can_left，波特率为1000000，并激活**

遥操作命令：
```
cd ..
python lerobot/scripts/control_robot.py \
    --robot.type=piper \
    --robot.inference_time=false \
    --control.type=teleoperate
```

#### 连接主从臂
```bash
bash can_config.sh
```

# Record录制数据集
设置数据集路径
```bash
HF_USER=$PWD/data
# 检查路径是否正确
echo $HF_USER
```

```bash
python3 lerobot/scripts/control_robot.py \
    --robot.type=piper \
    --robot.inference_time=false \
    --control.type=record \
    --control.fps=30 \
    --control.single_task="master_control_follower" \
    --control.repo_id=${HF_USER}/master_control_follower \
    --control.num_episodes=10 \
    --control.warmup_time_s=2 \
    --control.episode_time_s=30 \
    --control.reset_time_s=40 \
    --control.play_sounds=true \
    --control.push_to_hub=false
```

在录制过程中，随时按下右方向键 → 可提前结束当前 episode 并进入环境重置阶段。
在重置过程中，再次按下右方向键 → 可提前结束重置并开始录制下一个 episode。
在录制或重置的任何阶段，按下左方向键 ← 将提前终止当前 episode，并重新录制该 episode。
在录制过程中，按下 ESC 键 将立即结束整个录制流程，直接进入视频编码和数据集上传阶段。

# 注意

1. 录制数据集时，尽量让机械臂关节初始位置在[0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0]
2. 使用piper末端的零重力示教按钮进行操控
3. 修改lerobot/common/robot_devices/robots/configs.py中的机械臂和相机，可以使用find_camera_cv.py脚本快速找到相机地址

# 训练(以ACT为例)
```bash
python lerobot/scripts/train.py \
  --dataset.repo_id=${HF_USER}/master_control_follower \
  --policy.type=act \
  --output_dir=outputs/train/master_control_follower \
  --job_name=master_control_follower \
  --device=cuda \
  --wandb.enable=true
``` 


# 推理

```bash
python lerobot/scripts/control_robot.py \
    --robot.type=piper \
    --robot.inference_time=true \
    --control.type=record \
    --control.fps=30 \
    --control.single_task="master_control_follower" \
    --control.repo_id=$USER/eval_master_control_follower \
    --control.num_episodes=1 \
    --control.warmup_time_s=2 \
    --control.episode_time_s=30 \
    --control.reset_time_s=10 \
    --control.push_to_hub=false \
    --control.policy.path=outputs/train/master_control_follower/checkpoints/010000/pretrained_model
```

