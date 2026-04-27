-- สร้าง database สำหรับ MLflow แยกจาก Airflow
-- script นี้ run อัตโนมัติตอน postgres container เริ่มครั้งแรก
CREATE DATABASE mlflow
    WITH OWNER airflow
    ENCODING 'UTF8'
    LC_COLLATE = 'en_US.utf8'
    LC_CTYPE   = 'en_US.utf8';