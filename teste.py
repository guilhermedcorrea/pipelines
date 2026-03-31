
@paineis_bp.route("/painel-detalhes/<int:codponto>", methods=["GET"])
def painel_detalhes(codponto: int):


    sql_painel_dim = text("""
        SELECT TOP 1
            TRY_CONVERT(int, CodPonto) AS CodPonto,
            TRY_CONVERT(int, QuantidadeFaces) AS QuantidadeFaces,
            CAST(ISNULL(Tipo,'') AS varchar(120)) AS Tipo,
            CAST(ISNULL(Cidade,'') AS varchar(150)) AS Municipio,
            CAST(ISNULL(UF,'') AS varchar(10)) AS UF,
            CAST(ISNULL(Logradouro,'') AS varchar(250)) AS Logradouro,
            CAST(ISNULL(Numero,'') AS varchar(50)) AS Numero,
            CAST(ISNULL(Bairro,'') AS varchar(150)) AS Bairro,
            CAST(ISNULL(CEP,'') AS varchar(20)) AS CEP,
            CAST(ISNULL(Sentido,'') AS varchar(200)) AS Sentido,
            TRY_CONVERT(float, REPLACE(CAST(Latitude AS varchar(64)), ',', '.')) AS lat,
            TRY_CONVERT(float, REPLACE(CAST(Longitude AS varchar(64)), ',', '.')) AS lng,
            CAST(ISNULL(FormatoLxA,'') AS varchar(80)) AS FormatoLxA,
            CAST(ISNULL(Exibidora,'') AS varchar(150)) AS Exibidora,
            CAST(ISNULL(BitIluminado, 0) AS bit) AS BitIluminado,
            CAST(ISNULL(BitAtivo, 1) AS bit) AS BitAtivo,
            CAST(ISNULL(BitAluguel, 0) AS bit) AS BitAluguel
        FROM [Integracao].[Silver].[DimPaineisEuromidia]
        WHERE TRY_CONVERT(int, CodPonto) = :cod_ponto
    """)
    row_painel = db.session.execute(sql_painel_dim, {"cod_ponto": codponto}).mappings().first()

    if (not row_painel) or (row_painel.get("lat") is None) or (row_painel.get("lng") is None):
        sql_painel_fallback = text("""
            SELECT TOP 1
                TRY_CONVERT(int, CodPonto) AS CodPonto,
                CAST(ISNULL(Logr_Pr,'') AS varchar(200)) AS Logradouro,
                CAST(ISNULL(Tipo_Logr_Pr,'') AS varchar(80)) AS Tipo_Logradouro,
                CAST(ISNULL(BAIRRO,'') AS varchar(150)) AS Bairro,
                CAST(ISNULL([MUNICÍPIO],'') AS varchar(150)) AS Municipio,
                CAST(ISNULL(UF,'') AS varchar(10)) AS UF,
                CAST(ISNULL(CEP,'') AS varchar(20)) AS CEP,
                CAST(ISNULL(Sentido,'') AS varchar(200)) AS Sentido,
                CAST(ISNULL(TipoProd,'') AS varchar(120)) AS Tipo,
                CAST(ISNULL(CodFace,'') AS varchar(50)) AS CodFace,
                CAST(ISNULL(Iluminado,'') AS varchar(50)) AS Iluminado,
                TRY_CONVERT(float, REPLACE(CAST(LatD AS varchar(64)), ',', '.')) AS lat,
                TRY_CONVERT(float, REPLACE(CAST(LonD AS varchar(64)), ',', '.')) AS lng
            FROM [DataMining].[dbo].[CadastroPaineisEuromidia]
            WHERE TRY_CONVERT(int, CodPonto) = :cod_ponto
        """)
        row_fb = db.session.execute(sql_painel_fallback, {"cod_ponto": codponto}).mappings().first()
        if not row_fb or row_fb.get("lat") is None or row_fb.get("lng") is None:
            abort(404, description=f"Painel {codponto} não encontrado ou sem Latitude/Longitude válidos.")

        painel = {
            "id_painel": int(row_fb["CodPonto"]),
            "nome": f"Painel {int(row_fb['CodPonto'])}",
            "endereco": (
                f"{row_fb.get('Tipo_Logradouro','')} {row_fb.get('Logradouro','')}, "
                f"{row_fb.get('Bairro','')}, {row_fb.get('Municipio','')}-{row_fb.get('UF','')}"
            ).strip().strip(","),
            "formato": row_fb.get("Tipo") or "",
            "status": "Disponível",
            "lat": float(row_fb["lat"]),
            "lng": float(row_fb["lng"]),
            "url_ficha": f"/admin/paineis/{int(row_fb['CodPonto'])}",
            "uf": (row_fb.get("UF") or "").strip(),
            "municipio": (row_fb.get("Municipio") or "").strip(),
            "bairro": (row_fb.get("Bairro") or "").strip(),
            "cep": (row_fb.get("CEP") or "").strip(),
            "sentido": (row_fb.get("Sentido") or "").strip(),
            "tipo": (row_fb.get("Tipo") or "").strip(),
            "quantidade_faces": None,
            "exibidora": None,
            "bit_iluminado": None,
            "bit_ativo": None,
            "bit_aluguel": None,
            "formato_lxa": None,
        }
    else:
        endereco_painel = (
            f"{row_painel.get('Logradouro','')}"
            + (f", {row_painel.get('Numero','')}" if (row_painel.get("Numero") or "").strip() else "")
            + (f" • {row_painel.get('Bairro','')}" if (row_painel.get("Bairro") or "").strip() else "")
            + (f" • {row_painel.get('Municipio','')}-{row_painel.get('UF','')}" if (row_painel.get("Municipio") or "").strip() else "")
            + (f" • CEP {row_painel.get('CEP','')}" if (row_painel.get("CEP") or "").strip() else "")
        ).strip()

        painel = {
            "id_painel": int(row_painel["CodPonto"]),
            "nome": f"Painel {int(row_painel['CodPonto'])}",
            "endereco": endereco_painel,
            "formato": row_painel.get("Tipo") or "",
            "status": "Disponível" if bool(row_painel.get("BitAtivo") is True) else "Inativo",
            "lat": float(row_painel["lat"]),
            "lng": float(row_painel["lng"]),
            "url_ficha": f"/admin/paineis/{int(row_painel['CodPonto'])}",
            "uf": (row_painel.get("UF") or "").strip(),
            "municipio": (row_painel.get("Municipio") or "").strip(),
            "bairro": (row_painel.get("Bairro") or "").strip(),
            "cep": (row_painel.get("CEP") or "").strip(),
            "sentido": (row_painel.get("Sentido") or "").strip(),
            "tipo": (row_painel.get("Tipo") or "").strip(),
            "quantidade_faces": int(row_painel["QuantidadeFaces"]) if row_painel.get("QuantidadeFaces") is not None else None,
            "exibidora": (row_painel.get("Exibidora") or "").strip(),
            "bit_iluminado": bool(row_painel.get("BitIluminado") is True),
            "bit_ativo": bool(row_painel.get("BitAtivo") is True),
            "bit_aluguel": bool(row_painel.get("BitAluguel") is True),
            "formato_lxa": (row_painel.get("FormatoLxA") or "").strip(),
        }

  
    try:
        dt_ini_str = request.args.get("dt_ini") or request.args.get("dtIni") or request.args.get("data_ini") or ""
        dt_fim_str = request.args.get("dt_fim") or request.args.get("dtFim") or request.args.get("data_fim") or ""

        if not dt_ini_str:
            dt_ini_str = "2024-01-01"
        if not dt_fim_str:
            dt_fim_str = "2026-12-01"

        def _primeiro_dia_mes(iso: str, fallback: str) -> str:
            try:
                d = datetime.strptime(iso, "%Y-%m-%d")
                return f"{d.year:04d}-{d.month:02d}-01"
            except Exception:
                return fallback

        def _to_float_br(v):
            if v is None:
                return None
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).strip()
            if not s:
                return None
            if "," in s and "." in s:
                s = s.replace(".", "").replace(",", ".")
            elif "," in s and "." not in s:
                s = s.replace(",", ".")
            try:
                return float(s)
            except Exception:
                return None

        dt_ini_mes = _primeiro_dia_mes(dt_ini_str, "2024-01-01")
        dt_fim_mes = _primeiro_dia_mes(dt_fim_str, "2026-12-01")

     
        sql_financeiro = text("""
            ;WITH BaseItens AS (
                SELECT
                    TRY_CONVERT(int, i.CodPonto) AS CodPonto,
                    DATEFROMPARTS(
                        YEAR(COALESCE(TRY_CONVERT(date, i.Referencia), TRY_CONVERT(date, i.DataLancamento))),
                        MONTH(COALESCE(TRY_CONVERT(date, i.Referencia), TRY_CONVERT(date, i.DataLancamento))),
                        1
                    ) AS DataRef,
                    TRY_CONVERT(float, i.FaturamentoLiquidoFinalMensal) AS ReceitaLiquidaMensal
                FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] i
                WHERE TRY_CONVERT(int, i.CodPonto) = :cod_ponto
                  AND COALESCE(TRY_CONVERT(date, i.Referencia), TRY_CONVERT(date, i.DataLancamento)) IS NOT NULL
            ),
            ReceitaMes_Full AS (
                SELECT
                    CodPonto,
                    DataRef,
                    SUM(COALESCE(ReceitaLiquidaMensal, 0.0)) AS ReceitaMes
                FROM BaseItens
                GROUP BY CodPonto, DataRef
            ),
            ReceitaMes_Periodo AS (
                SELECT
                    CodPonto,
                    DataRef,
                    SUM(COALESCE(ReceitaLiquidaMensal, 0.0)) AS ReceitaMes
                FROM BaseItens
                WHERE DataRef >= :dt_ini
                  AND DataRef <= :dt_fim
                GROUP BY CodPonto, DataRef
            )
            SELECT
                r.CodPonto,
                r.DataRef,
                YEAR(r.DataRef) AS Ano,
                MONTH(r.DataRef) AS Mes,

               
                r.ReceitaMes AS ReceitaMes_Full,
                rp.ReceitaMes AS ReceitaMes_Periodo,

                ca.ValorMensal AS CustoMensal,
                ca.Ano AS AnoCusto,
                ca.Mes AS MesCusto
            FROM ReceitaMes_Full r
            LEFT JOIN ReceitaMes_Periodo rp
                ON rp.CodPonto = r.CodPonto
               AND rp.DataRef  = r.DataRef

            OUTER APPLY (
                SELECT TOP 1
                    TRY_CONVERT(int, c.Ano) AS Ano,
                    TRY_CONVERT(int, c.Mes) AS Mes,
                    TRY_CONVERT(float, REPLACE(CAST(c.ValorMensal AS varchar(64)), ',', '.')) AS ValorMensal
                FROM [Integracao].[Silver].[DimCustoMensalPainel] c
                WHERE TRY_CONVERT(int, c.CodPonto) = r.CodPonto
                  AND (
                        (TRY_CONVERT(int, c.Ano) * 100 + TRY_CONVERT(int, c.Mes))
                        <=
                        (YEAR(r.DataRef) * 100 + MONTH(r.DataRef))
                  )
                ORDER BY (TRY_CONVERT(int, c.Ano) * 100 + TRY_CONVERT(int, c.Mes)) DESC
            ) ca
            ORDER BY r.DataRef ASC;
        """)

        linhas_fin = db.session.execute(
            sql_financeiro,
            {
                "cod_ponto": int(painel["id_painel"]),
                "dt_ini": dt_ini_mes,
                "dt_fim": dt_fim_mes,
            }
        ).mappings().all()

       
        serie_fin = []
       
        serie_hist = []

        for r in linhas_fin:
            data_ref_iso = (r.get("DataRef").isoformat() if r.get("DataRef") else None)
            ano_i = int(r.get("Ano") or 0)
            mes_i = int(r.get("Mes") or 0)

            # FULL -> matriz
            receita_full = float(r.get("ReceitaMes_Full") or 0.0)

            custo_raw = r.get("CustoMensal")
            custo = float(custo_raw) if custo_raw is not None else None

            margem_full = None
            if receita_full > 0 and custo is not None:
                margem_full = ((receita_full - custo) / receita_full) * 100.0

            serie_hist.append({
                "data_ref": data_ref_iso,
                "ano": ano_i,
                "mes": mes_i,
                "receita": receita_full,
                "custo": custo,
                "margem_pct": float(margem_full) if margem_full is not None else None,
            })

            # PERÍODO -> gráfico (só entra se tiver receita no período)
            receita_periodo = r.get("ReceitaMes_Periodo")
            if receita_periodo is not None:
                receita_p = float(receita_periodo or 0.0)

                margem_p = None
                if receita_p > 0 and custo is not None:
                    margem_p = ((receita_p - custo) / receita_p) * 100.0

                serie_fin.append({
                    "data_ref": data_ref_iso,
                    "ano": ano_i,
                    "mes": mes_i,
                    "receita": receita_p,
                    "custo": custo,
                    "margem_pct": float(margem_p) if margem_p is not None else None,
                })

   
        sql_custo_categoria = text("""
            ;WITH BaseCat AS (
                SELECT
                    TRY_CONVERT(int, CodPonto) AS CodPonto,
                    TRY_CONVERT(int, Ano) AS Ano,
                    TRY_CONVERT(int, Mes) AS Mes,
                    DATEFROMPARTS(TRY_CONVERT(int, Ano), TRY_CONVERT(int, Mes), 1) AS DataRef,
                    CAST(ISNULL(Categoria,'') AS varchar(160)) AS Categoria,
                    TRY_CONVERT(float, ValorMensal) AS ValorMensal
                FROM [Integracao].[Silver].[DimCustoCategoriaMensalPainel]
                WHERE TRY_CONVERT(int, CodPonto) = :cod_ponto
                  AND TRY_CONVERT(int, Ano) IS NOT NULL
                  AND TRY_CONVERT(int, Mes) IS NOT NULL
            ),
            DentroPeriodo AS (
                SELECT *
                FROM BaseCat
                WHERE DataRef >= :dt_ini_mes
                  AND DataRef <= :dt_fim_mes
            ),
            UltimoMes AS (
                SELECT TOP 1 Ano, Mes
                FROM DentroPeriodo
                ORDER BY (Ano * 100 + Mes) DESC
            )
            SELECT
                d.Categoria,
                MAX(COALESCE(d.ValorMensal, 0.0)) AS Valor
            FROM DentroPeriodo d
            INNER JOIN UltimoMes u
                ON d.Ano = u.Ano AND d.Mes = u.Mes
            GROUP BY d.Categoria
            HAVING MAX(COALESCE(d.ValorMensal, 0.0)) > 0
            ORDER BY MAX(COALESCE(d.ValorMensal, 0.0)) DESC;
        """)

        linhas_cat = db.session.execute(
            sql_custo_categoria,
            {
                "cod_ponto": int(painel["id_painel"]),
                "dt_ini_mes": dt_ini_mes,
                "dt_fim_mes": dt_fim_mes,
            }
        ).mappings().all()

        custos_painel = []
        for r in linhas_cat:
            nome = (r.get("Categoria") or "").strip() or "Sem Categoria"
            valor = _to_float_br(r.get("Valor")) or 0.0
            custos_painel.append({"nome": nome, "valor": float(valor)})

    
        anos_presentes = sorted({x["ano"] for x in serie_hist if x.get("ano")}, reverse=True)

        matriz = []
        acumulado_geral = 0.0

        for ano in anos_presentes:
            linha = {
                "ano": ano,
                "painel": painel.get("nome") or f"Painel {painel.get('id_painel')}",
                "meses": {m: {"receita": None, "margem_pct": None} for m in range(1, 13)},
                "total_ano": 0.0,
                "margem_media_ano": None,
                "acumulado": None,
            }

            itens_ano = [x for x in serie_hist if x.get("ano") == ano and x.get("mes")]
            soma_receita_ano = 0.0
            soma_lucro_ano = 0.0
            soma_receita_margem = 0.0

            for it in itens_ano:
                mes = int(it["mes"])
                receita_it = float(it["receita"] or 0.0)

                custo_it = it.get("custo")
                custo_it_num = float(custo_it) if custo_it is not None else None

                linha["meses"][mes]["receita"] = receita_it
                linha["meses"][mes]["margem_pct"] = it.get("margem_pct")

                soma_receita_ano += receita_it

                if custo_it_num is not None:
                    soma_lucro_ano += (receita_it - custo_it_num)
                    soma_receita_margem += receita_it

            linha["total_ano"] = soma_receita_ano

            if soma_receita_margem > 0:
                linha["margem_media_ano"] = (soma_lucro_ano / soma_receita_margem) * 100.0

            acumulado_geral += soma_receita_ano
            linha["acumulado"] = acumulado_geral

            matriz.append(linha)

        fin_json = {
            "dt_ini": dt_ini_str,
            "dt_fim": dt_fim_str,
            "serie": serie_fin,      # ✅ FILTRADA (gráfico)
            "matriz": matriz,        # ✅ HISTÓRICO TOTAL (matriz)
            "custos": custos_painel  # ✅ FILTRADO (rosca)
        }

    except Exception:
        fin_json = {
            "dt_ini": None,
            "dt_fim": None,
            "serie": [],
            "matriz": [],
            "custos": [],
        }


    prospects_mock = [
        {
            "id_empresa": 102,
            "nome": "Empresa Teste 2",
            "segmento": "Supermercado",
            "lat": -22.8995573,
            "lng": -47.0285836,
            "score": 72,
            "ultimo_contato": None,
            "url_ficha": "/admin/empresas/102",
            "contratou_este_painel": False,
            "ja_e_cliente_euromidia": False,
        },
        {
            "id_empresa": 103,
            "nome": "Empresa Teste 3",
            "segmento": "Automotivo",
            "lat": -23.55240,
            "lng": -46.63460,
            "score": 66,
            "ultimo_contato": "2025-12-18",
            "url_ficha": "/admin/empresas/103",
            "contratou_este_painel": False,
            "ja_e_cliente_euromidia": False,
        },
    ]

    sql_clientes_euromidia = text("""
        ;WITH ContratouEstePainel AS (
            SELECT DISTINCT
                c.IDEmpresa AS IDEmpresa
            FROM [Integracao].[Silver].[FatoControleContratosEuromidia] c
            INNER JOIN [Integracao].[Silver].[FatoControleContratosItensEuromidia] i
                ON i.IDFatoControleContratoEuromidia = c.IDFatoControleContratosEuromidia
            WHERE TRY_CONVERT(int, i.CodPonto) = :cod_ponto
        )
        SELECT
            e.IDEmpresa,
            CAST(COALESCE(e.NomeFantasia, e.RazaoSocial, '') AS nvarchar(200)) AS Nome,
            CAST(ISNULL(e.DescricaoCnae, '') AS nvarchar(250)) AS Segmento,
            e.CNPJ,
            CAST(ISNULL(e.UF,'') AS varchar(10)) AS UF,
            CAST(ISNULL(e.Municipio,'') AS nvarchar(150)) AS Municipio,
            CAST(ISNULL(e.Bairro,'') AS nvarchar(150)) AS Bairro,
            CAST(ISNULL(e.CEP,'') AS varchar(20)) AS CEP,
            CASE WHEN p.IDEmpresa IS NOT NULL THEN 1 ELSE 0 END AS ContratouEstePainel
        FROM [Integracao].[Silver].[DimEmpresas] e
        LEFT JOIN ContratouEstePainel p
            ON p.IDEmpresa = e.IDEmpresa
        WHERE e.IDEmpresaProprietaria = 3
    """)

    linhas_clientes = db.session.execute(
        sql_clientes_euromidia,
        {"cod_ponto": int(painel["id_painel"])}
    ).mappings().all()

    clientes_reais = []
    for r in linhas_clientes:
        clientes_reais.append({
            "id_empresa": int(r["IDEmpresa"]),
            "nome": r.get("Nome") or "",
            "segmento": r.get("Segmento") or "",
            "lat": None,
            "lng": None,
            "score": None,
            "ultimo_contato": None,
            "url_ficha": f"/admin/empresas/{int(r['IDEmpresa'])}",
            "contratou_este_painel": bool(r.get("ContratouEstePainel") == 1),
            "ja_e_cliente_euromidia": True,
            "cnpj": r.get("CNPJ"),
            "uf": r.get("UF"),
            "municipio": r.get("Municipio"),
            "bairro": r.get("Bairro"),
            "cep": r.get("CEP"),
        })

    empresas = clientes_reais + prospects_mock

    raio_m = int(request.args.get("raio_m", "1000"))
    status = request.args.get("status", "todos")
    segmento = request.args.get("segmento", "todos")

    def _distancia_haversine_m(lat1, lng1, lat2, lng2):
        if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
            return None
        R = 6371000.0
        lat1r = math.radians(float(lat1))
        lng1r = math.radians(float(lng1))
        lat2r = math.radians(float(lat2))
        lng2r = math.radians(float(lng2))
        dlat = lat2r - lat1r
        dlng = lng2r - lng1r
        a = (math.sin(dlat / 2) ** 2) + (math.cos(lat1r) * math.cos(lat2r) * (math.sin(dlng / 2) ** 2))
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return int(round(R * c))

    def _classificar_relacao(emp: dict) -> str:
        if emp.get("contratou_este_painel"):
            return "cliente_no_painel"
        if emp.get("ja_e_cliente_euromidia"):
            return "cliente_euromidia"
        return "prospect"

    def _classificar_proximidade(distancia_m: int, raio_m: int) -> str:
        if distancia_m is None:
            return "sem_localizacao"
        return "dentro_do_raio" if distancia_m <= raio_m else "fora_do_raio"

    def _definir_camada(status_relacao: str, proximidade: str) -> str:
        if status_relacao == "cliente_no_painel":
            return "clientes_painel"
        if status_relacao == "cliente_euromidia":
            return "clientes_euromidia"
        if proximidade == "dentro_do_raio":
            return "prospects_raio"
        return "fora_escopo"

    empresas_enriquecidas = []
    for emp in empresas:
        dist_m = _distancia_haversine_m(painel["lat"], painel["lng"], emp.get("lat"), emp.get("lng"))
        status_relacao = _classificar_relacao(emp)
        proximidade = _classificar_proximidade(dist_m, raio_m)
        camada = _definir_camada(status_relacao, proximidade)

        emp2 = dict(emp)
        emp2["distancia_m"] = dist_m
        emp2["status_relacao"] = status_relacao
        emp2["proximidade"] = proximidade
        emp2["camada"] = camada
        emp2["status"] = "cliente" if status_relacao in ("cliente_no_painel", "cliente_euromidia") else "prospect"
        empresas_enriquecidas.append(emp2)

    def _passa_filtro(emp: dict) -> bool:
        if emp.get("camada") == "fora_escopo":
            return False
        if segmento != "todos" and (emp.get("segmento") or "") != segmento:
            return False
        if status == "todos":
            return True
        if status == "cliente":
            return emp.get("status") == "cliente"
        if status == "prospect":
            return emp.get("status") == "prospect"
        if status == "cliente_no_painel":
            return emp.get("status_relacao") == "cliente_no_painel"
        if status == "cliente_euromidia":
            return emp.get("status_relacao") == "cliente_euromidia"
        return True

    empresas_filtradas = [e for e in empresas_enriquecidas if _passa_filtro(e)]

    camadas = {
        "clientes_painel": [e for e in empresas_filtradas if e.get("camada") == "clientes_painel"],
        "clientes_euromidia": [e for e in empresas_filtradas if e.get("camada") == "clientes_euromidia"],
        "prospects_raio": [e for e in empresas_filtradas if e.get("camada") == "prospects_raio"],
        "sem_localizacao": [e for e in empresas_filtradas if e.get("proximidade") == "sem_localizacao"],
    }

    empresas_no_raio = [
        e for e in empresas_enriquecidas
        if e.get("proximidade") == "dentro_do_raio" and e.get("camada") != "fora_escopo"
    ]
    clientes_no_raio = [e for e in empresas_no_raio if e.get("status") == "cliente"]

    cont_segmentos = {}
    for e in empresas_no_raio:
        seg = e.get("segmento") or "Sem Segmento"
        cont_segmentos[seg] = cont_segmentos.get(seg, 0) + 1

    segmento_top = None
    if cont_segmentos:
        segmento_top = sorted(cont_segmentos.items(), key=lambda x: x[1], reverse=True)[0][0]

    kpis = {
        "empresas_no_raio": len(empresas_no_raio),
        "clientes_no_raio": len(clientes_no_raio),
        "segmento_top": segmento_top,
        "clientes_sem_localizacao": len(camadas["sem_localizacao"]),
        "painel_faces": painel.get("quantidade_faces"),
        "painel_iluminado": painel.get("bit_iluminado"),
        "painel_ativo": painel.get("bit_ativo"),
        "painel_aluguel": painel.get("bit_aluguel"),
    }

    sql_bairros = text("""
        SELECT
            CAST(ISNULL(Bairro,'') AS varchar(160)) AS bairro_final,
            TRY_CONVERT(float, LatitudeBairro) AS lat,
            TRY_CONVERT(float, LongitudeBairro) AS lng,

            TRY_CONVERT(float, TotalEnderecos) AS total_enderecos,
            TRY_CONVERT(float, QuantidadeResidencial) AS qtd_residencial,
            TRY_CONVERT(float, QuantidadeNaoResidencial) AS qtd_nao_residencial,
            TRY_CONVERT(float, QuantidadeIndefinido) AS qtd_indefinido,

            TRY_CONVERT(float, PercentualResidencial) AS pct_residencial,
            TRY_CONVERT(float, PercentualNaoResidencial) AS pct_nao_residencial,
            TRY_CONVERT(float, PercentualIndefinido) AS pct_indefinido,

            TRY_CONVERT(float, PesoHeat) AS peso_heat,
            CAST(ISNULL(PerfilDominante,'') AS varchar(60)) AS perfil_dominante,

            TRY_CONVERT(float, QuantidadePaineis) AS qtd_paineis,
            CAST(ISNULL(ListaCodPonto,'') AS varchar(max)) AS lista_cod_ponto,
            CAST(ISNULL(BitPainelEuromidia, 0) AS bit) AS tem_painel_euromidia
        FROM [Integracao].[Silver].[DimPerfilRegiaoFull]
        WHERE UF = :uf
          AND Municipio = :municipio
          AND LatitudeBairro IS NOT NULL
          AND LongitudeBairro IS NOT NULL
    """)

    linhas_bairros = db.session.execute(
        sql_bairros,
        {"uf": painel["uf"], "municipio": painel["municipio"]}
    ).mappings().all()

    cep_points = []
    for r in linhas_bairros:
        lat_i = r.get("lat")
        lng_i = r.get("lng")
        if lat_i is None or lng_i is None:
            continue

        tot_end = float(r.get("total_enderecos") or 0.0)
        tot_nres = float(r.get("qtd_nao_residencial") or 0.0)

        dens_100 = 0.0
        if tot_end > 0:
            dens_100 = (tot_nres / tot_end) * 100.0

        cep_points.append({
            "lat": float(lat_i),
            "lng": float(lng_i),
            "props": {
                "BAIRRO": r.get("bairro_final") or "",
                "TOT_END": int(round(float(r.get("total_enderecos") or 0.0))),
                "TOT_DP": int(round(float(r.get("qtd_residencial") or 0.0))),
                "TOT_ESTAB_NRESID": int(round(float(r.get("qtd_nao_residencial") or 0.0))),
                "PCT_RESIDENCIAL_%": float(r.get("pct_residencial") or 0.0),
                "PCT_ESTAB_NRESID_%": float(r.get("pct_nao_residencial") or 0.0),
                "DENS_ESTAB_NRESID_100_END": float(dens_100),
                "PESO_HEAT": float(r.get("peso_heat") or 0.0),
                "TIPO_AREA": r.get("perfil_dominante") or "",
                "QTD_PAINEIS": int(round(float(r.get("qtd_paineis") or 0.0))),
                "TEM_PAINEL_EUROMIDIA": bool(r.get("tem_painel_euromidia") is True),
                "LISTA_COD_PONTO": r.get("lista_cod_ponto") or "",
                "LISTA_CEP_PAINEL": r.get("lista_cep_painel") or "",
            }
        })

    return render_template(
        "euromidia/mapa_mercado.html",
        painel_json=painel,
        empresas_json=empresas_filtradas,
        cep_points_json=cep_points,
        filtro_inicial={"raio_m": raio_m, "status": status, "segmento": segmento},
        camadas=camadas,
        kpis=kpis,
        fin_json=fin_json,
    )



