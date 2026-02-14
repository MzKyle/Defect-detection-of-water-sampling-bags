import os
import glob
from pathlib import Path
from cryptography.fernet import Fernet

class WeightEncryptor:
    def __init__(self, key=None):
        self.key = key or Fernet.generate_key()
        self.cipher = Fernet(self.key)

    @staticmethod
    def find_latest_exp(root="./runs/train6"):
        # 获取最新实验目录
        exp_dirs = sorted(glob.glob(os.path.join(root, "exp*")),
                         key=lambda x: os.path.getmtime(x),
                         reverse=True)
        return Path(exp_dirs[0]) if exp_dirs else None

    def encrypt_weights(self, suffix=".enc"):
        # 定位最新权重目录
        exp_dir = self.find_latest_exp()
        if not exp_dir:
            raise FileNotFoundError("未找到训练输出目录")
        
        weights_dir = exp_dir / "weights"
        # 加密所有pt文件
        for pt_file in weights_dir.glob("*.pt"):
            # 读取原始权重
            with open(pt_file, "rb") as f:
                original_data = f.read()
            
            # 加密并写入新文件
            encrypted_data = self.cipher.encrypt(original_data)
            encrypted_path = pt_file.with_suffix(suffix)
            with open(encrypted_path, "wb") as f:
                f.write(encrypted_data)
            
            # 删除原始文件
            os.remove(pt_file)
            print(f"加密完成: {pt_file} -> {encrypted_path}")

        # 保存密钥到实验目录
        key_path = exp_dir / "model.key"
        with open(key_path, "wb") as f:
            f.write(self.key)
        print(f"密钥已保存到: {key_path}")

def auto_encrypt():
    encryptor = WeightEncryptor()
    encryptor.encrypt_weights()




# if __name__ == "__main__":
#     opt = parse_opt()
#     main(opt)  # 原有训练代码
    
#     # 添加自动加密 ------------------
#     from weight_crypto import auto_encrypt
#     auto_encrypt()



