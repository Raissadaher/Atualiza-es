from PyQt5.QtCore import QVariant
from PyQt5.QtGui import QColor
from qgis.core import (
    QgsApplication,
    QgsProject,
    QgsVectorLayer,
    QgsField,
    QgsCoordinateReferenceSystem
)
from qgis.analysis import QgsNativeAlgorithms
import qgis.processing
import processing
import unicodedata


class Interseccao:
    def __init__(self):
        # Registra o provedor de algoritmos nativo
        QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())

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
        """Adiciona campo 'Area_ha' com cálculo da área"""
        if layer is None or not layer.isValid():
            return
        try:
            layer.startEditing()
            if 'Area_ha' not in [f.name() for f in layer.fields()]:
                layer.addAttribute(QgsField('Area_ha', QVariant.Double))
                layer.updateFields()

            idx = layer.fields().indexFromName('Area_ha')
            for feat in layer.getFeatures():
                geom = feat.geometry()
                if geom and not geom.isEmpty():
                    area_ha = geom.area() / 10000
                    layer.changeAttributeValue(feat.id(), idx, round(area_ha, 4))
            layer.commitChanges()
        except Exception as e:
            print(f"❌ Erro ao calcular área da camada '{layer.name()}': {e}")

    def corrigir_geometria(self, layer):
        if layer is None or not layer.isValid():
            return None
        try:
            res = processing.run("native:fixgeometries", {
                'INPUT': layer,
                'OUTPUT': 'memory:'
            })
            return res['OUTPUT']
        except Exception as e:
            print(f"❌ Erro ao corrigir geometria da camada '{layer.name()}': {e}")
            return layer

    def reprojetar_para(self, layer, crs_destino_authid):
        if layer is None or not layer.isValid():
            return None
        try:
            crs_destino = QgsCoordinateReferenceSystem(crs_destino_authid)
            if layer.crs().authid() == crs_destino_authid:
                return layer
            res = processing.run("native:reprojectlayer", {
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
    def executar(self):
        project = QgsProject.instance()
        layers = list(project.mapLayers().values())

        # ------------------------
        # Tenta detectar camadas ambientais (APP / RL / Área de Supressão)
        # ------------------------
        nomes_ambientais = [
            "área de supressão",
            "Área de Preservação Permanente",
            "Reserva legal"
        ]
        camadas_ambientais = []

        for nome in nomes_ambientais:
            for layer in layers:
                if self.normalizar_texto(nome) in self.normalizar_texto(layer.name()):
                    camadas_ambientais.append(layer)

        # ------------------------
        # Se não houver camadas ambientais, usa Camada01–04
        # ------------------------
        camadas_genericas = []
        for nome in ["Camada01", "Camada02", "Camada03", "Camada04"]:
            for layer in layers:
                if self.normalizar_texto(nome) in self.normalizar_texto(layer.name()):
                    camadas_genericas.append(layer)

        # ------------------------
        # PROCESSAMENTO AMBIENTAL
        # ------------------------
        if len(camadas_ambientais) >= 2:
            print("🟤 Processamento ambiental detectado...")
            camada_base = camadas_ambientais[0]
            crs_base = camada_base.crs().authid()
            camada_base_corr = self.corrigir_geometria(self.reprojetar_para(camada_base, crs_base))

            layer_app = None
            layer_rl = None
            layer_fora = None

            # --- Interseção APP ---
            if len(camadas_ambientais) >= 2:
                overlay_app = camadas_ambientais[1]
                overlay_app_corr = self.corrigir_geometria(self.reprojetar_para(overlay_app, crs_base))

                try:
                    layer_app = processing.run("native:intersection", {
                        'INPUT': camada_base_corr,
                        'OVERLAY': overlay_app_corr,
                        'OUTPUT': 'memory:'
                    })['OUTPUT']
                    layer_app.setName("Área de supressão em APP")
                    self.adicionar_campo_area(layer_app)
                    QgsProject.instance().addMapLayer(layer_app)
                except Exception as e:
                    print(f"❌ Erro na interseção APP: {e}")

                # Diferença restante
                try:
                    camada_base_corr = processing.run("native:difference", {
                        'INPUT': camada_base_corr,
                        'OVERLAY': layer_app if layer_app else overlay_app_corr,
                        'OUTPUT': 'memory:'
                    })['OUTPUT']
                except Exception as e:
                    print(f"❌ Erro na diferença APP: {e}")

            # --- Interseção RL ---
            if len(camadas_ambientais) >= 3:
                overlay_rl = camadas_ambientais[2]
                overlay_rl_corr = self.corrigir_geometria(self.reprojetar_para(overlay_rl, crs_base))

                try:
                    layer_rl = processing.run("native:intersection", {
                        'INPUT': overlay_rl_corr,
                        'OVERLAY': camada_base_corr,
                        'OUTPUT': 'memory:'
                    })['OUTPUT']
                    layer_rl.setName("Área de supressão em RL")
                    self.adicionar_campo_area(layer_rl)
                    QgsProject.instance().addMapLayer(layer_rl)
                except Exception as e:
                    print(f"❌ Erro na interseção RL: {e}")

                # Diferença fora
                try:
                    layer_fora = processing.run("native:difference", {
                        'INPUT': camada_base_corr,
                        'OVERLAY': layer_rl if layer_rl else overlay_rl_corr,
                        'OUTPUT': 'memory:'
                    })['OUTPUT']
                    layer_fora.setName("Área de supressão fora")
                    self.adicionar_campo_area(layer_fora)
                    QgsProject.instance().addMapLayer(layer_fora)
                except Exception as e:
                    print(f"❌ Erro na diferença RL/Fora: {e}")
            else:
                layer_fora = camada_base_corr
                layer_fora.setName("Área de supressão fora")
                self.adicionar_campo_area(layer_fora)
                QgsProject.instance().addMapLayer(layer_fora)

        # ------------------------
        # PROCESSAMENTO GENÉRICO (Camada01–04)
        # ------------------------
        elif len(camadas_genericas) >= 2:
            print("🟢 Processamento genérico detectado...")
            crs_base = camadas_genericas[0].crs().authid()
            camadas_corr = [self.corrigir_geometria(self.reprojetar_para(l, crs_base)) for l in camadas_genericas]

            # Diferença iterativa para "Fora Total"
            exclusivas = []
            for i, base in enumerate(camadas_corr):
                outras = [c for j, c in enumerate(camadas_corr) if j != i]
                temp = base
                for o in outras:
                    temp = processing.run("native:difference", {'INPUT': temp, 'OVERLAY': o, 'OUTPUT': 'memory:'})['OUTPUT']
                exclusivas.append(temp)

            # Mescla exclusivas
            merge_params = {
                'LAYERS': exclusivas,
                'CRS': camadas_genericas[0].crs(),
                'OUTPUT': 'memory:'
            }
            fora_total = processing.run("native:mergevectorlayers", merge_params)['OUTPUT']
            fora_total.setName("Fora Total")
            self.adicionar_campo_area(fora_total)
            QgsProject.instance().addMapLayer(fora_total)

        else:
            print("⚠️ Nenhuma camada válida encontrada para processamento.")
