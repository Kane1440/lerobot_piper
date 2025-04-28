import time
from dataclasses import replace

import torch

from lerobot.common.robot_devices.cameras.utils import \
    make_cameras_from_configs
from lerobot.common.robot_devices.motors.utils import (
    get_motor_names, make_motors_buses_from_configs)
from lerobot.common.robot_devices.robots.configs import PiperRobotConfig
from lerobot.common.robot_devices.utils import (
    RobotDeviceAlreadyConnectedError, RobotDeviceNotConnectedError)


class PiperRobot:
    def __init__(self, config: PiperRobotConfig | None = None, **kwargs):
        if config is None:
            config = PiperRobotConfig()
        # Overwrite config arguments using kwargs
        self.config = replace(config, **kwargs)
        self.robot_type = self.config.type
        self.inference_time = self.config.inference_time # if it is inference time
        
        # 构建相机
        self.cameras = make_cameras_from_configs(self.config.cameras)
        
        # 构建从臂
        self.follower_motors = make_motors_buses_from_configs(self.config.follower_arm)
        self.follower_arm = self.follower_motors['main']

        # 构建主臂（仅在非推理模式下作为输入设备）
        if not self.inference_time and hasattr(self.config, "master_arm"):
            self.master_motors = make_motors_buses_from_configs(self.config.master_arm)
            self.master_arm = self.master_motors['main']
        else:
            self.master_motors = {}
            self.master_arm = None
        
        # 手柄遥控功能
        # if not self.inference_time:
        #     self.teleop = SixAxisArmController()
        # else:
        #     self.teleop = None
        
        self.logs = {}
        self.is_connected = False

    @property
    def camera_features(self) -> dict:
        cam_ft = {}
        for cam_key, cam in self.cameras.items():
            key = f"observation.images.{cam_key}"
            cam_ft[key] = {
                "shape": (cam.height, cam.width, cam.channels),
                "names": ["height", "width", "channels"],
                "info": None,   
            }
        return cam_ft

    
    @property
    def motor_features(self) -> dict:
        action_names = get_motor_names(self.follower_motors)
        state_names = get_motor_names(self.follower_motors)
        return {
            "action": {
                "dtype": "float32",
                "shape": (len(action_names),),
                "names": action_names,
            },
            "observation.state": {
                "dtype": "float32",
                "shape": (len(state_names),),
                "names": state_names,
            },
        }
    
    @property
    def has_camera(self):
        return len(self.cameras) > 0

    @property
    def num_cameras(self):
        return len(self.cameras)


    def connect(self) -> None:
        """Connect piper and cameras"""
        if self.is_connected:
            raise RobotDeviceAlreadyConnectedError(
                "Piper is already connected. Do not run robot.connect() twice."
            )
        
        # connect piper
        self.follower_arm.connect(enable=True)
        if self.master_arm is not None:
            self.master_arm.connect(enable=True)  
        print("piper connected")

        # connect cameras
        for name in self.cameras:
            self.cameras[name].connect()
            self.is_connected = self.is_connected and self.cameras[name].is_connected
            print(f"camera {name} connected")
        
        print("All connected")
        self.is_connected = True
        
        self.run_calibration()


    def disconnect(self) -> None:
        """move to home position, disenable piper and cameras"""
        # move piper to home position, disable
        # if not self.inference_time:
        #     self.teleop.stop()

        # disconnect piper
        self.follower_arm.safe_disconnect()
        if self.master_arm is not None:
            self.master_arm.safe_disconnect_master()
        print("piper disable after 5 seconds")
        time.sleep(5)
        self.follower_arm.connect(enable=False)
        if self.master_arm is not None:
            self.master_arm.connect(enable=False)

        # disconnect cameras
        if len(self.cameras) > 0:
            for cam in self.cameras.values():
                cam.disconnect()

        self.is_connected = False


    def run_calibration(self):
        """move piper to the home position"""
        if not self.is_connected:
            raise ConnectionError()
        
        self.follower_arm.apply_calibration()
        if self.master_arm is not None:
            self.master_arm.apply_calibration_master()
        # if not self.inference_time:
        #     self.teleop.reset()



    def teleop_step(
        self, record_data=False
    ) -> None | tuple[dict[str, torch.Tensor], dict[str, torch.Tensor]]:
        if not self.is_connected:
            raise ConnectionError()
        
        # 从主臂读取目标角度（动作）
        before_read_t = time.perf_counter()
        if self.master_arm is not None:
            action_state_raw = self.master_arm.read()  # 主臂发出的动作（用于控制从臂）
        else:
            raise ValueError("Master arm not initialized in teleop mode.")
        self.logs["read_master_pos_dt_s"] = time.perf_counter() - before_read_t

        # 单位转换：0.001° ➜ rad
        joint_factor = 57324.840764
        action_state = {
            k: v / joint_factor if k != "gripper" else v / 1_000_000
            for k, v in action_state_raw.items()
        }

        # 发送到从臂执行
        before_write_t = time.perf_counter()
        target_joints = list(action_state.values())
        self.follower_arm.write(target_joints)
        self.logs["write_follower_pos_dt_s"] = time.perf_counter() - before_write_t

        if not record_data:
            return

        # 记录从臂的真实反馈状态（state）和主臂作为 action
        follower_state = self.follower_arm.read()

        # 转为 torch tensor
        state_tensor = torch.as_tensor(list(follower_state.values()), dtype=torch.float32)
        action_tensor = torch.as_tensor(list(action_state.values()), dtype=torch.float32)

        # 采集图像
        images = {}
        for name in self.cameras:
            before_camread_t = time.perf_counter()
            images[name] = self.cameras[name].async_read()
            images[name] = torch.from_numpy(images[name])
            self.logs[f"read_camera_{name}_dt_s"] = self.cameras[name].logs["delta_timestamp_s"]
            self.logs[f"async_read_camera_{name}_dt_s"] = time.perf_counter() - before_camread_t

        # 组装 obs 和 action
        obs_dict, action_dict = {}, {}
        obs_dict["observation.state"] = state_tensor
        action_dict["action"] = action_tensor
        for name in self.cameras:
            obs_dict[f"observation.images.{name}"] = images[name]

        return obs_dict, action_dict



    def send_action(self, action: torch.Tensor) -> torch.Tensor:
        """Write the predicted actions from policy to the motors"""
        if not self.is_connected:
            raise RobotDeviceNotConnectedError(
                "Piper is not connected. You need to run `robot.connect()`."
            )

        # send to motors, torch to list
        target_joints = action.tolist()
        # print("sending action:%s",str(target_joints))
        self.follower_arm.write(target_joints)

        return action



    def capture_observation(self) -> dict:
        """capture current images and joint positions"""
        if not self.is_connected:
            raise RobotDeviceNotConnectedError(
                "Piper is not connected. You need to run `robot.connect()`."
            )
        
        # read current joint positions
        before_read_t = time.perf_counter()
        state = self.follower_arm.read()  # 6 joints + 1 gripper
        self.logs["read_pos_dt_s"] = time.perf_counter() - before_read_t

        state = torch.as_tensor(list(state.values()), dtype=torch.float32)

        # read images from cameras
        images = {}
        for name in self.cameras:
            before_camread_t = time.perf_counter()
            images[name] = self.cameras[name].async_read()
            images[name] = torch.from_numpy(images[name])
            self.logs[f"read_camera_{name}_dt_s"] = self.cameras[name].logs["delta_timestamp_s"]
            self.logs[f"async_read_camera_{name}_dt_s"] = time.perf_counter() - before_camread_t

        # Populate output dictionaries and format to pytorch
        obs_dict = {}
        obs_dict["observation.state"] = state
        for name in self.cameras:
            obs_dict[f"observation.images.{name}"] = images[name]
        return obs_dict
    
    def teleop_safety_stop(self):
        """ move to home position after record one episode """
        self.run_calibration()

    
    def __del__(self):
        if self.is_connected:
            self.disconnect()
            # if not self.inference_time:
            #     self.teleop.stop()
