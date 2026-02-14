import os
from pathlib import Path
from cryptography.fernet import Fernet

class LegacyEncryptor:
    def __init__(self, exp_root):
        self.exp_root = Path(exp_root)
        if not self.exp_root.exists():
            raise ValueError(f"实验目录不存在: {self.exp_root}")

    def encrypt_experiment(self, exp_name):
        """加密单个实验目录"""
        exp_dir = self.exp_root / exp_name
        if not exp_dir.exists():
            print(f"跳过不存在的实验目录: {exp_name}")
            return

        weights_dir = exp_dir / "weights"
        if not weights_dir.exists():
            print(f"{exp_name} 无weights目录，跳过")
            return

        # 生成密钥
        key = Fernet.generate_key()
        cipher = Fernet(key)
        
        # 加密所有pt文件
        encrypted_files = []
        for pt_file in weights_dir.glob("*.pt"):
            # 加密
            with open(pt_file, "rb") as f:
                data = f.read()
            encrypted_data = cipher.encrypt(data)
            
            # 保存加密文件
            enc_file = pt_file.with_suffix(".enc")
            with open(enc_file, "wb") as f:
                f.write(encrypted_data)
            
            # 删除原始文件
            # os.remove(pt_file)
            # encrypted_files.append(enc_file.name)
        
        # 保存密钥到实验目录
        key_file = exp_dir / "model.key"
        with open(key_file, "wb") as f:
            f.write(key)
        
        print(f"加密完成: {exp_name}")
        print(f"生成加密文件: {', '.join(encrypted_files)}")
        print(f"密钥位置: {key_file}\n")

    def batch_encrypt(self, exp_prefix="exp"):
        """批量加密所有匹配的实验目录"""
        exp_dirs = sorted([
            d.name for d in self.exp_root.iterdir() 
            if d.is_dir() and d.name.startswith(exp_prefix)
        ], key=lambda x: int(x[len(exp_prefix):]) if x[len(exp_prefix):].isdigit() else 0)

        for exp_name in exp_dirs:
            self.encrypt_experiment(exp_name)

if __name__ == "__main__":
    # 使用示例 ----------------------------------------------------
    # EXP_ROOT = "/home/ysj/ncf/yolov5/runs/train"  # 修改为实际路径
    EXP_ROOT = r"D:\code\yolov5\runs\train7"  # 修改为实际路径

    
    encryptor = LegacyEncryptor(EXP_ROOT)
    
    # 加密单个实验
    encryptor.encrypt_experiment("exp")  
    
    # 加密所有exp开头的实验目录
    # encryptor.batch_encrypt()