# db.py
import mysql.connector
from mysql.connector import Error

def insert_detection_result(backup_path, yolo_result, status,
                            host='localhost', user='root', 
                            password='123456', database='detection_db'):
    """
    保存检测记录：
    :param backup_path: 备份图片的完整路径
    :param yolo_result: YOLO 检测的坐标信息（JSON字符串）
    :param status: 检测状态，"异常" 或 "正常"
    """
    try:
        connection = mysql.connector.connect(
            host=host,
            user=user,
            password=password,
            database=database
        )
        cursor = connection.cursor()
        
        # 查询当前表中记录数量，当记录数达到100条时清空整个表
        count_query = "SELECT COUNT(*) FROM detection_results"
        cursor.execute(count_query)
        count = cursor.fetchone()[0]
        if count >= 100:
            delete_query = "DELETE FROM detection_results"
            cursor.execute(delete_query)
            connection.commit()
            print("记录已达到100条，已清空表中的数据。")
        
        # 执行插入操作
        query = "INSERT INTO detection_results (backup_path, result, status) VALUES (%s, %s, %s)"
        cursor.execute(query, (backup_path, yolo_result, status))
        connection.commit()
        print("MySQL 插入成功:", (backup_path, yolo_result, status))
    except Error as e:
        print("MySQL 错误:", e)
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
