from __future__ import annotations

from src.db.repositories.arquivo_upload import ArquivoUploadRepository
from src.db.repositories.base import BaseRepository
from src.db.repositories.bloco import BlocoRepository
from src.db.repositories.deteccao import DeteccaoRepository
from src.db.repositories.faixa import FaixaRepository
from src.db.repositories.job import JobRepository
from src.db.repositories.parametro_km import ParametroKmRepository
from src.db.repositories.projeto import ProjetoRepository
from src.db.repositories.resultado_analise import ResultadoAnaliseRepository
from src.db.repositories.viagem import ViagemRepository

__all__ = [
    "ArquivoUploadRepository",
    "BaseRepository",
    "BlocoRepository",
    "DeteccaoRepository",
    "FaixaRepository",
    "JobRepository",
    "ParametroKmRepository",
    "ProjetoRepository",
    "ResultadoAnaliseRepository",
    "ViagemRepository",
]
