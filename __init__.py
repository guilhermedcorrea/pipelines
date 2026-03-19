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