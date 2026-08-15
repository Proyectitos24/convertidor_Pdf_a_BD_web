"""
Compensación de mejor esfuerzo para el hueco "subida a R2 correcta,
pero el INSERT posterior en Supabase falla": nunca se invoca si la
subida a R2 nunca llegó a completarse (la llamada a esta función ya
presupone que la subida terminó bien).

CASO AMBIGUO A EVITAR: que el INSERT lance una excepción no significa
necesariamente que la fila no se haya creado — puede haberse confirmado
en Supabase y haberse perdido solo la respuesta (timeout, corte de red).
Si en ese caso se borrara el objeto de R2 a ciegas, quedaría una fila
válida en Supabase apuntando a un archivo que ya no existe. Por eso,
antes de borrar nada, se comprueba explícitamente si la fila llegó a
existir realmente.
"""

from services.r2_service import delete_object


def ejecutar_con_compensacion_r2(object_key, funcion_insert, funcion_existe):
    """
    Ejecuta funcion_insert() (la subida a R2 ya debe haber terminado
    con éxito antes de llamar a esto). Si funcion_insert() lanza una
    excepción, se decide si borrar object_key de R2 según lo que
    devuelva funcion_existe() (sin argumentos; el llamador ya la deja
    cerrada sobre el store_id/object_key correctos de esa subida):

      - funcion_existe() devuelve True  -> el INSERT sí se confirmó
        (solo se perdió la respuesta): NO se borra nada en R2.
      - funcion_existe() devuelve False -> el INSERT realmente no llegó
        a crear la fila: se intenta borrar object_key de R2 (mejor
        esfuerzo).
      - funcion_existe() lanza una excepción, o devuelve cualquier cosa
        que no sea exactamente True/False -> resultado ambiguo: NO se
        borra nada, por precaución (es preferible dejar un objeto
        huérfano en R2 que romper una fila válida de Supabase).

    En todos los casos, la excepción ORIGINAL de funcion_insert() se
    vuelve a lanzar siempre. Ni un fallo de funcion_existe() ni un
    fallo del propio borrado sustituyen u ocultan esa excepción
    original.

    Devuelve lo que devuelva funcion_insert() si no hay error.
    """
    try:
        return funcion_insert()
    except Exception:
        try:
            fila_existe = funcion_existe()
        except Exception:
            fila_existe = None  # ambiguo: no se borra

        if fila_existe is False:
            try:
                delete_object(object_key)
            except Exception:
                pass
        # fila_existe is True, o cualquier valor no concluyente (None,
        # o algo distinto de True/False): no se toca R2.

        raise
