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




#Exibe Containers em execão
#docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"



#Log
#docker logs --since=30m pipelines-celery-airflow-worker-1 2>&1 | grep -iE "APROVACAO_CONTRATO|tarefa_processar_aprovacao_contrato|Traceback|Exception|ERROR|erro|410"



#docker compose down --remove-orphans

#docker compose build --no-cache flask-app-1 flask-app-2 celery-checking-worker celery-kanban-worker

#usar esse


#docker compose build --no-cache flask-app-1 flask-app-2 celery-checking-worker celery-kanban-worker celery-clientes-worker celery-paineis-worker
#docker compose build --no-cache flask-app-1 flask-app-2 celery-checking-worker celery-kanban-worker celery-clientes-worker celery-paineis-worker celery-airflow-worker

#docker compose up -d

#docker compose logs -f nginx-flask flask-app-1 flask-app-2
#docker compose logs -f nginx-flask flask-app-1 flask-app-2 celery-paineis-worker


#Reinicia o flask

#docker compose restart flask-app-1 flask-app-2



####usar


#docker compose down --remove-orphans


#docker compose build --no-cache flask-app-1 flask-app-2 celery-checking-worker celery-kanban-worker celery-clientes-worker celery-paineis-worker celery-airflow-worker celery-contratos-worker celery-anexos-contratos-worker


#sem no cache
#docker compose build flask-app-1 flask-app-2 celery-checking-worker celery-kanban-worker celery-clientes-worker celery-paineis-worker celery-airflow-worker celery-contratos-worker celery-anexos-contratos-worker

#COMPOSE_PARALLEL_LIMIT=1 COMPOSE_BAKE=false BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker compose build --no-cache flask-app-1 flask-app-2 celery-checking-worker celery-kanban-worker celery-clientes-worker celery-paineis-worker celery-airflow-worker celery-contratos-worker celery-anexos-contratos-worker

#docker compose up -d






"""
cd ~/projetos/pipelines

echo "==== AIRFLOW_UID usado no compose ===="
grep AIRFLOW_UID .env || true

echo "==== Corrigindo permissão das pastas do Airflow ===="
AIRFLOW_UID_ATUAL=$(grep '^AIRFLOW_UID=' .env | cut -d '=' -f2)

if [ -z "$AIRFLOW_UID_ATUAL" ]; then
  AIRFLOW_UID_ATUAL=50000
fi

echo "Usando AIRFLOW_UID=$AIRFLOW_UID_ATUAL"

sudo chown -R ${AIRFLOW_UID_ATUAL}:0 ./airflow/dags
sudo chown -R ${AIRFLOW_UID_ATUAL}:0 ./airflow/logs
sudo chown -R ${AIRFLOW_UID_ATUAL}:0 ./airflow/plugins
sudo chown -R ${AIRFLOW_UID_ATUAL}:0 ./airflow/config
sudo chown -R ${AIRFLOW_UID_ATUAL}:0 ./airflow/src

sudo find ./airflow/dags -type d -exec chmod 775 {} \;
sudo find ./airflow/dags -type f -name "*.py" -exec chmod 664 {} \;

sudo find ./airflow/logs -type d -exec chmod 775 {} \;
sudo find ./airflow/plugins -type d -exec chmod 775 {} \;
sudo find ./airflow/config -type d -exec chmod 775 {} \;
sudo find ./airflow/src -type d -exec chmod 775 {} \;

echo "==== Desativando .airflowignore se existir ===="
find ./airflow/dags -name ".airflowignore" -print -exec mv {} {}.bak \;

echo "==== Forçando alteração dos arquivos DAG ===="
find ./airflow/dags -type f -name "*.py" -exec touch {} \;

echo "==== Reiniciando serviços do Airflow ===="
docker compose restart airflow-dag-processor airflow-scheduler airflow-apiserver airflow-worker airflow-triggerer

echo "==== Esperando containers subirem ===="
sleep 20

echo "==== Conferindo se o dag-processor consegue ler as permissões ===="
docker compose exec -T airflow-dag-processor bash -lc "
id
echo ''
ls -ld /opt/airflow/dags
ls -ld /opt/airflow/dags/Euromidia
ls -ld /opt/airflow/dags/Euromidia/comercial
ls -l /opt/airflow/dags/Euromidia/comercial/pipeline_indice_ooh.py
"

echo "==== Forçando serialização das DAGs ===="
docker compose exec -T airflow-dag-processor airflow dags reserialize -v

echo "==== Listando DAGs ===="
docker compose exec -T airflow-dag-processor airflow dags list

"""