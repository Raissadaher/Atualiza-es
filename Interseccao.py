from PyQt5.QtCore import QVariant
from qgis.core import (
    QgsApplication, QgsProject, QgsVectorLayer, QgsField,
    QgsCoordinateReferenceSystem
)
from qgis.analysis import QgsNativeAlgorithms
import qgis.processing
import unicodedata


class Interseccao:
    def __init__(self):
        # Garante que o provedor de algoritmos nativo esteja registrado
        QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())

    def executar(self):
        """Método principal de execução"""
        self.processamento()

    # ------------------------
    # Funções auxiliares
    # ------------------------
    def normalizar_texto(self, texto):
        """Remove acentos e converte para minúsculas"""
        if not texto:
            return ""
        return ''.join(
            c for c in unicodedata.normalize('NFD', texto.lower())
            if unicodedata.category(c) != 'Mn'
        )

    def adicionar_campo_area(self, layer):
        """Adiciona um campo 'Area_ha' com o cálculo da área"""
        try:
            if layer is None or not layer.isValid():
                print("⚠️ Camada inválida ao calcular área.")
                return

            layer.startEditing()
            if 'Area_ha' not in [f.name() for f in layer.fields()]:
                layer.addAttribute(QgsField('Area_ha', QVariant.Double))
                layer.updateFields()

            idx = layer.fields().indexFromName('Area_ha')
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if geom and not geom.isEmpty():
                    area_ha = geom.area() / 10000  # converte m² para hectares
                    layer.changeAttributeValue(feat.id(), idx, round(area_ha, 4))
            layer.commitChanges()
        except Exception as e:
            print(f"❌ Erro ao calcular área da camada '{layer.name()}': {e}")

    def corrigir_geometria(self, layer):
        """Corrige geometrias inválidas"""
        if layer is None or not layer.isValid():
            print("⚠️ Camada inválida ao corrigir geometria.")
            return None
        try:
            res = qgis.processing.run("native:fixgeometries", {
                'INPUT': layer,
                'OUTPUT': 'memory:'
            })
            return res['OUTPUT']
        except Exception as e:
            print(f"❌ Erro ao corrigir geometrias da camada '{layer.name()}': {e}")
            return layer

    def reprojetar_para(self, layer, crs_destino_authid):
        """Reprojeta uma camada para o CRS desejado"""
        if layer is None or not layer.isValid():
            print("⚠️ Camada inválida para reprojeção.")
            return None

        try:
            crs_destino = QgsCoordinateReferenceSystem(crs_destino_authid)
            if layer.crs().authid() == crs_destino_authid:
                print(f"ℹ️ {layer.name()}: CRS já está em {crs_destino_authid}, reprojeção ignorada.")
                return layer

            res = qgis.processing.run("native:reprojectlayer", {
                'INPUT': layer,
                'TARGET_CRS': crs_destino,
                'OUTPUT': 'memory:'
            })
            return res['OUTPUT']
        except Exception as e:
            print(f"❌ Erro ao reprojetar camada '{layer.name()}': {e}")
            return layer

    # ------------------------
    # Processamento principal
    # ------------------------
    def processamento(self):
        project = QgsProject.instance()
        layers = list(project.mapLayers().values())

        # Detecta camadas ambientais prioritárias
        nomes_prioritarios = [
            "área de supressão",
            "Área de Preservação Permanente",
            "Reserva legal"
        ]
        camadas = []

        for nome in nomes_prioritarios:
            for layer in layers:
                if self.normalizar_texto(nome) in self.normalizar_texto(layer.name()):
                    camadas.append(layer)

        # Verifica se são camadas genéricas (Camada01–04)
        modo_generico = False
        if len(camadas) < 2:
            camadas = []
            for nome in ["Camada01", "Camada02", "Camada03", "Camada04"]:
                for layer in layers:
                    if self.normalizar_texto(nome) in self.normalizar_texto(layer.name()):
                        camadas.append(layer)
            modo_generico = True

        # Exige no mínimo duas camadas
        if len(camadas) < 2:
            print("⚠️ É necessário pelo menos duas camadas para o processamento.")
            return

        camada_base = camadas[0]
        crs_base = camada_base.crs().authid()
        camada_base_corrigida = self.corrigir_geometria(
            self.reprojetar_para(camada_base, crs_base)
        )

        # ------------------------
        # MODO GENÉRICO (Camada01–04)
        # ------------------------
        if modo_generico:
            print("🟢 Modo genérico detectado: Camadas 01–04")

            resultado_camadas = []
            camada_corrente = camada_base_corrigida

            for i in range(1, len(camadas)):
                overlay = camadas[i]
                overlay_corr = self.corrigir_geometria(
                    self.reprojetar_para(overlay, crs_base)
                )

                try:
                    inter_res = qgis.processing.run("native:intersection", {
                        'INPUT': camada_corrente,
                        'OVERLAY': overlay_corr,
                        'OUTPUT': 'memory:'
                    })
                    layer_result = inter_res['OUTPUT']
                    layer_result.setName(f"Área Camada {i:02d}")

                    self.adicionar_campo_area(layer_result)
                    QgsProject.instance().addMapLayer(layer_result)
                    resultado_camadas.append(layer_result)
                except Exception as e:
                    print(f"❌ Erro na interseção da Camada {i:02d}: {e}")

            print("\n✅ Processamento concluído com sucesso (modo genérico).")
            for lyr in resultado_camadas:
                print(f" - {lyr.name()}")
            return

        # ------------------------
        # MODO AMBIENTAL (APP / RL / Fora)
        # ------------------------
        print(f"🟤 Modo ambiental detectado: usando '{camada_base.name()}' como base prioritária.")
        layer_app = None
        layer_rl = None
        layer_fora = None

        # --- APP ---
        if len(camadas) >= 2:
            overlay_app = camadas[1]
            overlay_app_corr = self.corrigir_geometria(
                self.reprojetar_para(overlay_app, crs_base)
            )
            try:
                inter_app_res = qgis.processing.run("native:intersection", {
                    'INPUT': camada_base_corrigida,
                    'OVERLAY': overlay_app_corr,
                    'OUTPUT': 'memory:'
                })
                layer_app = inter_app_res['OUTPUT']
                layer_app.setName("Área de supressão em APP")
            except Exception as e:
                print(f"❌ Erro na interseção com APP: {e}")

            # Remove partes já sobrepostas
            try:
                diff_app_res = qgis.processing.run("native:difference", {
                    'INPUT': camada_base_corrigida,
                    'OVERLAY': layer_app if layer_app else overlay_app_corr,
                    'OUTPUT': 'memory:'
                })
                camada_base_corrigida = diff_app_res['OUTPUT']
            except Exception as e:
                print(f"❌ Erro ao calcular diferença APP: {e}")

        # --- RL ---
        if len(camadas) >= 3:
            overlay_rl = camadas[2]
            overlay_rl_corr = self.corrigir_geometria(
                self.reprojetar_para(overlay_rl, crs_base)
            )
            try:
                inter_rl_res = qgis.processing.run("native:intersection", {
                    'INPUT': overlay_rl_corr,
                    'OVERLAY': camada_base_corrigida,
                    'OUTPUT': 'memory:'
                })
                layer_rl = inter_rl_res['OUTPUT']
                layer_rl.setName("Área de supressão em RL")
            except Exception as e:
                print(f"❌ Erro na interseção com RL: {e}")

            # Área fora (diferença final)
            try:
                diff_rl_res = qgis.processing.run("native:difference", {
                    'INPUT': camada_base_corrigida,
                    'OVERLAY': layer_rl if layer_rl else overlay_rl_corr,
                    'OUTPUT': 'memory:'
                })
                layer_fora = diff_rl_res['OUTPUT']
                layer_fora.setName("Área de supressão fora")
            except Exception as e:
                print(f"❌ Erro ao calcular diferença RL: {e}")
                layer_fora = camada_base_corrigida
        else:
            layer_fora = camada_base_corrigida
            layer_fora.setName("Área de supressão fora")

        # --- Adiciona as camadas ao projeto ---
        for lyr in [layer_app, layer_rl, layer_fora]:
            if lyr:
                self.adicionar_campo_area(lyr)
                QgsProject.instance().addMapLayer(lyr)

        print("\n✅ Processamento concluído com sucesso! Camadas finais adicionadas:")
        if layer_app: print(" - Área de supressão em APP")
        if layer_rl: print(" - Área de supressão em RL")
        if layer_fora: print(" - Área de supressão fora")
