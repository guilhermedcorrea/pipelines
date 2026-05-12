#docker stop $(docker ps -aq) 2>/dev/null
#docker rm -f $(docker ps -aq) 2>/dev/null
#docker rmi -f $(docker images -aq) 2>/dev/null
#docker volume rm $(docker volume ls -q) 2>/dev/null
#docker network prune -f
#docker builder prune -a -f


#verificar
#docker ps -a
#docker images
#docker volume ls


#Reinicia
#sudo systemctl restart docker || sudo service docker restart

#rebuild
#docker compose build --no-cache
#docker compose up airflow-init
#docker compose up -d

#verificar se subiu
#docker compose logs -f
#docker compose logs -f airflow-apiserver
#docker compose exec airflow-apiserver cat /opt/airflow/simple_auth_manager_passwords.json.generated

#Ver a senha gerada dentro do Airflow
#cat /home/guilherme_correa/PythonJobs/pipelines/airflow/simple_auth/simple_auth_manager_passwords.json.generated

#ver senhas:
#cat /home/guilherme_correa/PythonJobs/pipelines/airflow/simple_auth/simple_auth_manager_passwords.json.generated

#deleta arquivo de senha
#rm -f /home/guilherme_correa/PythonJobs/pipelines/airflow/simple_auth/simple_auth_manager_passwords.json.generated

#python3 -c "import secrets; print(secrets.token_hex(32))"
#python3 -c "import secrets; print(secrets.token_hex(32))"


#Reiniciar
#docker compose restart airflow-scheduler airflow-worker airflow-dag-processor



#parar e iniciar:
#docker compose down
#docker compose up -d


#Liberar no PowerShell
#netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=8080 connectaddress=172.26.200.69 connectport=8080
#New-NetFirewallRule -DisplayName "Airflow 8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow



#Rebuild
#docker compose down --remove-orphans
#docker compose up -d --build --force-recreate
#docker compose ps


#Logs Flask
#docker compose logs --tail=100 nginx-flask
#docker compose logs --tail=100 flask-app


#rebuild flask

#cd /home/guilherme_correa/PythonJobs/pipelines
#docker compose build --no-cache flask-app
#docker compose up -d flask-app nginx-flask



#Liberar Flask no Power Shell


#netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=5000 connectaddress=172.26.200.69 connectport=5000
#New-NetFirewallRule -DisplayName "Flask 5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow


#reinicia
#docker compose restart airflow-apiserver airflow-scheduler airflow-worker airflow-dag-processor





#Acessa o container
#docker exec -it pipelines-airflow-apiserver-1 bash



#ver log flask
#docker compose restart flask-app
#reiniciar flask com nginx
#docker compose restart flask-app nginx-flask



"""
docker compose down
docker compose build --no-cache airflow-init airflow-apiserver airflow-scheduler airflow-dag-processor airflow-worker airflow-triggerer
docker compose up airflow-init
docker compose up -d
docker compose exec airflow-apiserver airflow users create \
  --username guilherme \
  --firstname Guilherme \
  --lastname Correa \
  --role Admin \
  --email guilherme.correa@cscc.com.br \
  --password Gui@07spk8abcde
  """



#acessa containwer
#docker compose exec airflow-apiserver bash


#rebuild docker compose
#docker compose up -d --build

#Inicia
#docker compose up


#Reinicia flask
#docker compose up -d --build flask-app


#docker compose build flask-app
#docker compose up -d flask-app


#reinicia o airflow
#docker compose restart


#docker compose up -d --build flask-app

#docker compose up -d --build --force-recreate flask-app nginx-flask



#rebuild compose
#cd /home/guilherme_correa/PythonJobs/pipelines
#docker compose down
#docker compose up -d --build


#docker compose logs -f flask-app


#Reduild e restart compose
#docker compose down
#docker compose up -d --build



#rebuild com celery


#docker compose down
#docker compose build --no-cache flask-app celery-checking-worker
#docker compose up -d

##verificar log


#docker compose logs -f celery-checking-worker






#Novo Ajuste com Redis, celery e socket

#docker compose down
#docker compose build --no-cache flask-app celery-checking-worker celery-kanban-worker
#docker compose up -d



#Limpar cache builder

#wsl -d Ubuntu-24.04 -- docker builder prune -a -f




#Limpando cache Docker

#wsl -d Ubuntu-24.04 -- docker builder prune -a -f   (limpa cache)

#wsl -d Ubuntu-24.04 -- docker system df (verificação)

#wsl -d Ubuntu-24.04 -- df -h (verificação)



#wsl --shutdown (desliga o WSL)




#Get-ChildItem "$env:LOCALAPPDATA\wsl" -Recurse -Filter ext4.vhdx | Select-Object FullName, Length (descobre o caminho do ext4.vhdx)




#wsl -d Ubuntu-24.04 -- sudo fstrim -av (Roda TRIM dentro do WSL)



#diskpart (acessa Diskpart)


#e executa o comando para desanexar


#select vdisk file="C:\Users\Guilherme Correa\AppData\Local\wsl\{6a37620c-2e8a-45b6-b5ad-553e8d141179}\ext4.vhdx"
#attach vdisk readonly
#compact vdisk
#detach vdisk
#exit



#openssl rand -hex 32





#docker compose down --remove-orphans

#docker compose build --no-cache flask-app-1 flask-app-2 celery-checking-worker celery-kanban-worker

#usar esse


#docker compose build --no-cache flask-app-1 flask-app-2 celery-checking-worker celery-kanban-worker celery-clientes-worker celery-paineis-worker

#docker compose up -d

#docker compose logs -f nginx-flask flask-app-1 flask-app-2
#docker compose logs -f nginx-flask flask-app-1 flask-app-2 celery-paineis-worker