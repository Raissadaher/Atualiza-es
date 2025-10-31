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
        # Garante que o provedor nativo esteja registrado
        QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())

    # ---------------------------------
    # Funções auxiliares
    # ---------------------------------
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
                    area_ha = geom.area() / 10000
                    layer.changeAttributeValue(feat.id(), idx, round(area_ha, 4))
            layer.commitChanges()
        except Exception as e:
            print(f"❌ Erro ao calcular área da camada '{layer.name()}': {e}")

    def corrigir_geometria(self, layer):
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
        if layer is None or not layer.isValid():
            print("⚠️ Camada inválida para reprojeção.")
            return None
        try:
            crs_destino = QgsCoordinateReferenceSystem(crs_destino_authid)
            if layer.crs().authid() == crs_destino_authid:
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

    # ---------------------------------
    # Processamento principal
    # ---------------------------------
    def executar(self, camadas=None):
        """
        Calcula a diferença final ("Fora Total") entre várias camadas.
        Se camadas não forem passadas, busca automaticamente Camada01–04 no projeto.
        """
        project = QgsProject.instance()

        # Busca automática se necessário
        if camadas is None:
            camadas = []
            for nome in ["Camada01", "Camada02", "Camada03", "Camada04"]:
                for layer in project.mapLayers().values():
                    if self.normalizar_texto(nome) in self.normalizar_texto(layer.name()):
                        camadas.append(layer)

        if len(camadas) < 2:
            print("⚠️ É necessário pelo menos duas camadas para calcular 'Fora Total'.")
            return

        print(f"🟢 Processando {len(camadas)} camadas para 'Fora Total'...")

        # Corrige geometrias e reprojeta todas as camadas
        crs_base = camadas[0].crs().authid()
        camadas_corr = [self.corrigir_geometria(self.reprojetar_para(l, crs_base)) for l in camadas]

        # ---------------------------------
        # Diferença iterativa
        # ---------------------------------
        exclusivas = []
        for i, base in enumerate(camadas_corr):
            outras = [c for j, c in enumerate(camadas_corr) if j != i]
            temp = base
            for o in outras:
                temp = processing.run("native:difference", {'INPUT': temp, 'OVERLAY': o, 'OUTPUT': 'memory:'})['OUTPUT']
            exclusivas.append(temp)

        # ---------------------------------
        # Mescla todas as áreas exclusivas em uma camada final
        # ---------------------------------
        merge_params = {
            'LAYERS': exclusivas,
            'CRS': camadas[0].crs(),
            'OUTPUT': 'memory:'
        }
        fora_total = processing.run("native:mergevectorlayers", merge_params)['OUTPUT']
        fora_total.setName("Fora Total")
        self.adicionar_campo_area(fora_total)

        # Aplica estilo visual correto para polígonos
        simb = fora_total.renderer().symbol()
        simb.setColor(QColor(255, 0, 0, 120))           # preenchimento vermelho semi-transparente
        simb.symbolLayer(0).setStrokeColor(QColor(0, 0, 0))  # borda preta
        simb.symbolLayer(0).setStrokeWidth(0.5)

        QgsProject.instance().addMapLayer(fora_total)
        print("✅ Diferença final 'Fora Total' criada com sucesso!")
