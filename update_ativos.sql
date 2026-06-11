DECLARE @DataReferencia DATE = '2026-06-09';

DECLARE @InicioDia  DATETIME2(0) = CAST(@DataReferencia AS DATETIME2(0));
DECLARE @ProximoDia DATETIME2(0) = DATEADD(DAY, 1, @InicioDia);

UPDATE item
SET
    item.BitAtivo =
        CASE
            WHEN UPPER(LTRIM(RTRIM(ISNULL(item.AtivoCancelamento, '')))) = 'A'
                 AND item.DataInicioPrevisto < @ProximoDia
                 AND item.DataTerminoPrevisto >= @InicioDia
                THEN 1
            ELSE 0
        END,
    item.DataAtualizacao = GETDATE()
FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item;