import utils
from main import app

def route_dispatcher(request):
    routes = {
        '/api/data': handle_data_request,
        '/api/user': handle_user_request,
        '/api/validate': handle_validation_request
    }
    url = request.get('url')
    method = request.get('method')
    handler = routes.get((url, method))
    if handler:
        return handler(request)
    else:
        return response_formatter({'error': 'Not Found'})

def request_validator(request):
    is_valid, message = utils.validate_request(request)
    if not is_valid:
        return False, message
    return True, None

def response_formatter(data, error=None):
    if error:
        return {'error': error}
    return {'data': data}

def handle_data_request(request):
    is_valid, message = request_validator(request)
    if not is_valid:
        return response_formatter({}, message)
    return response_formatter({'message': 'Data request handled'})

def handle_user_request(request):
    is_valid, message = request_validator(request)
    if not is_valid:
        return response_formatter({}, message)
    return response_formatter({'message': 'User request handled'})

def handle_validation_request(request):
    is_valid, message = request_validator(request)
    if not is_valid:
        return response_formatter({}, message)
    return response_formatter({'message': 'Validation request handled'})

@app.route('/api/*', methods=['GET', 'POST', 'PUT', 'DELETE'])
def api_route():
    request = {'url': request.path, 'method': request.method}
    response = route_dispatcher(request)
    return response_formatter(response)