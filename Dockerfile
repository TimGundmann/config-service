FROM openjdk:10.0.2-13-jre-sid

COPY target/config-server.jar /app/config-server.jar

CMD ["java", "-jar", "/app/config-server.jar", "--spring.profiles.active=prod"]
